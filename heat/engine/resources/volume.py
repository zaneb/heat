# vim: tabstop=4 shiftwidth=4 softtabstop=4

#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from heat.openstack.common import log as logging

from heat.common import exception
from heat.engine import clients
from heat.engine import resource
from heat.engine import scheduler

logger = logging.getLogger(__name__)


class Volume(resource.Resource):
    properties_schema = {'AvailabilityZone': {'Type': 'String',
                                              'Required': True},
                         'Size': {'Type': 'Number'},
                         'SnapshotId': {'Type': 'String'},
                         'Tags': {'Type': 'List'}}

    def __init__(self, name, json_snippet, stack):
        super(Volume, self).__init__(name, json_snippet, stack)

    def handle_create(self):
        vol = self.cinder().volumes.create(
            self.properties['Size'],
            display_name=self.physical_resource_name(),
            display_description=self.physical_resource_name())
        self.resource_id_set(vol.id)

        return vol

    def check_active(self, vol):
        vol.get()

        if vol.status == 'available':
            return True
        elif vol.status == 'creating':
            return False
        else:
            raise exception.Error(vol.status)

    def handle_update(self, json_snippet):
        return self.UPDATE_REPLACE

    def handle_delete(self):
        if self.resource_id is not None:
            try:
                vol = self.cinder().volumes.get(self.resource_id)

                if vol.status == 'in-use':
                    logger.warn('cant delete volume when in-use')
                    raise exception.Error("Volume in use")

                self.cinder().volumes.delete(self.resource_id)
            except clients.cinderclient.exceptions.NotFound:
                pass


class VolumeAttachTask(object):
    """A task for attaching a volume to a Nova server."""

    def __init__(self, stack, server_id, volume_id, device):
        """
        Initialise with the stack (for obtaining the clients), ID of the
        server and volume, and the device name on the server.
        """
        self.clients = stack.clients
        self.server_id = server_id
        self.volume_id = volume_id
        self.device = device
        self.attachment_id = None

    def __str__(self):
        """Return a human-readable string description of the task."""
        return 'Attaching Volume %s to Instance %s as %s' % (self.volume_id,
                                                             self.server_id,
                                                             self.device)

    def __repr__(self):
        """Return a brief string description of the task."""
        return '%s(%s -> %s [%s])' % (type(self).__name__,
                                      self.volume_id,
                                      self.server_id,
                                      self.device)

    def __call__(self):
        """Return a co-routine which runs the task."""
        logger.debug(str(self))

        va = self.clients.nova().volumes.create_server_volume(
            server_id=self.server_id,
            volume_id=self.volume_id,
            device=self.device)
        self.attachment_id = va.id
        yield

        vol = self.clients.cinder().volumes.get(va.id)
        while vol.status == 'available' or vol.status == 'attaching':
            logger.debug('%s - volume status: %s' % (str(self), vol.status))
            yield
            vol.get()

        if vol.status != 'in-use':
            raise exception.Error(vol.status)

        logger.info('%s - complete' % str(self))


class VolumeDetachTask(object):
    """A task for attaching a volume to a Nova server."""

    def __init__(self, stack, server_id, volume_id):
        """
        Initialise with the stack (for obtaining the clients), and the IDs of
        the server and volume.
        """
        self.clients = stack.clients
        self.server_id = server_id
        self.volume_id = volume_id

    def __str__(self):
        """Return a human-readable string description of the task."""
        return 'Detaching Volume %s from Instance %s' % (self.volume_id,
                                                         self.server_id)

    def __repr__(self):
        """Return a brief string description of the task."""
        return '%s(%s -/> %s)' % (type(self).__name__,
                                  self.volume_id,
                                  self.server_id)

    def __call__(self):
        """Return a co-routine which runs the task."""
        logger.debug(str(self))

        try:
            vol = self.clients.cinder().volumes.get(self.volume_id)
        except clients.cinder_exceptions.NotFound:
            logger.warning('%s - volume not found' % str(self))
            return

        server_api = self.clients.nova().volumes

        try:
            server_api.delete_server_volume(self.server_id, self.volume_id)
        except clients.novaclient.exceptions.NotFound:
            logger.warning('%s - not found' % str(self))

        yield

        try:
            vol.get()
            while vol.status == 'in-use':
                logger.debug('%s - volume still in use' % str(self))
                yield

                try:
                    server_api.delete_server_volume(self.server_id,
                                                    self.volume_id)
                except clients.novaclient.exceptions.NotFound:
                    pass
                vol.get()

            logger.info('%s - status: %s' % (str(self), vol.status))

        except clients.cinderclient.exceptions.NotFound:
            logger.warning('%s - volume not found' % str(self))


class VolumeAttachment(resource.Resource):
    properties_schema = {'InstanceId': {'Type': 'String',
                                        'Required': True},
                         'VolumeId': {'Type': 'String',
                                      'Required': True},
                         'Device': {'Type': "String",
                                    'Required': True,
                                    'AllowedPattern': '/dev/vd[b-z]'}}

    def __init__(self, name, json_snippet, stack):
        super(VolumeAttachment, self).__init__(name, json_snippet, stack)

    def handle_create(self):
        server_id = self.properties['InstanceId']
        volume_id = self.properties['VolumeId']
        dev = self.properties['Device']

        attach_task = VolumeAttachTask(self.stack, server_id, volume_id, dev)
        attach_runner = scheduler.TaskRunner(attach_task)

        attach_runner.start()

        self.resource_id_set(attach_task.attachment_id)

        return attach_runner

    def check_active(self, attach_runner):
        return attach_runner.step()

    def handle_update(self, json_snippet):
        return self.UPDATE_REPLACE

    def handle_delete(self):
        server_id = self.properties['InstanceId']
        volume_id = self.properties['VolumeId']
        detach_task = VolumeDetachTask(self.stack, server_id, volume_id)
        scheduler.TaskRunner(detach_task)()


def resource_mapping():
    return {
        'AWS::EC2::Volume': Volume,
        'AWS::EC2::VolumeAttachment': VolumeAttachment,
    }
