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
            except clients.cinder_exceptions.NotFound:
                pass


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
        inst = self.stack.clients.attach_volume_to_instance(server_id,
                                                            volume_id,
                                                            dev)
        self.resource_id_set(inst)

    def handle_update(self, json_snippet):
        return self.UPDATE_REPLACE

    def handle_delete(self):
        server_id = self.properties['InstanceId']
        volume_id = self.properties['VolumeId']
        self.stack.clients.detach_volume_from_instance(server_id, volume_id)


def resource_mapping():
    return {
        'AWS::EC2::Volume': Volume,
        'AWS::EC2::VolumeAttachment': VolumeAttachment,
    }
