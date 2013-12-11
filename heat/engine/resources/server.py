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

from oslo.config import cfg

cfg.CONF.import_opt('instance_user', 'heat.common.config')

from heat.common import exception
from heat.engine import clients
from heat.engine import scheduler
from heat.engine.resources import nova_utils
from heat.engine import constraints
from heat.engine import properties
from heat.engine import resource
from heat.openstack.common.gettextutils import _
from heat.openstack.common import log as logging

logger = logging.getLogger(__name__)


class Server(resource.Resource):

    PROPERTIES = (
        NAME, IMAGE, BLOCK_DEVICE_MAPPING, FLAVOR,
        FLAVOR_UPDATE_POLICY, IMAGE_UPDATE_POLICY, KEY_NAME,
        ADMIN_USER, AVAILABILITY_ZONE, SECURITY_GROUPS, NETWORKS,
        SCHEDULER_HINTS, METADATA, USER_DATA_FORMAT, USER_DATA,
        RESERVATION_ID, CONFIG_DRIVE, DISK_CONFIG,
    ) = (
        'name', 'image', 'block_device_mapping', 'flavor',
        'flavor_update_policy', 'image_update_policy', 'key_name',
        'admin_user', 'availability_zone', 'security_groups', 'networks',
        'scheduler_hints', 'metadata', 'user_data_format', 'user_data',
        'reservation_id', 'config_drive', 'diskConfig',
    )

    _BLOCK_DEVICE_MAPPING_KEYS = (
        BLOCK_DEVICE_MAPPING_DEVICE_NAME, BLOCK_DEVICE_MAPPING_VOLUME_ID,
        BLOCK_DEVICE_MAPPING_SNAPSHOT_ID,
        BLOCK_DEVICE_MAPPING_VOLUME_SIZE,
        BLOCK_DEVICE_MAPPING_DELETE_ON_TERM,
    ) = (
        'device_name', 'volume_id',
        'snapshot_id',
        'volume_size',
        'delete_on_termination',
    )

    _NETWORK_KEYS = (
        NETWORK_UUID, NETWORK_FIXED_IP, NETWORK_PORT,
    ) = (
        'uuid', 'fixed_ip', 'port',
    )

    properties_schema = {
        NAME: properties.Schema(
            properties.Schema.STRING,
            _('Optional server name.')
        ),
        IMAGE: properties.Schema(
            properties.Schema.STRING,
            _('The ID or name of the image to boot with.'),
            update_allowed=True
        ),
        BLOCK_DEVICE_MAPPING: properties.Schema(
            properties.Schema.LIST,
            _('Block device mappings for this server.'),
            schema=properties.Schema(
                properties.Schema.MAP,
                schema={
                    BLOCK_DEVICE_MAPPING_DEVICE_NAME: properties.Schema(
                        properties.Schema.STRING,
                        _('A device name where the volume will be '
                          'attached in the system at /dev/device_name. '
                          'This value is typically vda.'),
                        required=True
                    ),
                    BLOCK_DEVICE_MAPPING_VOLUME_ID: properties.Schema(
                        properties.Schema.STRING,
                        _('The ID of the volume to boot from. Only one '
                          'of volume_id or snapshot_id should be '
                          'provided.')
                    ),
                    BLOCK_DEVICE_MAPPING_SNAPSHOT_ID: properties.Schema(
                        properties.Schema.STRING,
                        _('The ID of the snapshot to create a volume '
                          'from.')
                    ),
                    BLOCK_DEVICE_MAPPING_VOLUME_SIZE: properties.Schema(
                        properties.Schema.STRING,
                        _('The size of the volume, in GB. It is safe to '
                          'leave this blank and have the Compute service '
                          'infer the size.')
                    ),
                    BLOCK_DEVICE_MAPPING_DELETE_ON_TERM: properties.Schema(
                        properties.Schema.BOOLEAN,
                        _('Indicate whether the volume should be deleted '
                          'when the server is terminated.')
                    ),
                },
            )
        ),
        FLAVOR: properties.Schema(
            properties.Schema.STRING,
            _('The ID or name of the flavor to boot onto.'),
            required=True,
            update_allowed=True
        ),
        FLAVOR_UPDATE_POLICY: properties.Schema(
            properties.Schema.STRING,
            _('Policy on how to apply a flavor update; either by requesting '
              'a server resize or by replacing the entire server.'),
            default='RESIZE',
            constraints=[
                constraints.AllowedValues(['RESIZE', 'REPLACE']),
            ],
            update_allowed=True
        ),
        IMAGE_UPDATE_POLICY: properties.Schema(
            properties.Schema.STRING,
            _('Policy on how to apply an image-id update; either by '
              'requesting a server rebuild or by replacing the entire server'),
            default='REPLACE',
            constraints=[
                constraints.AllowedValues(['REBUILD', 'REPLACE']),
            ],
            update_allowed=True
        ),
        KEY_NAME: properties.Schema(
            properties.Schema.STRING,
            _('Name of keypair to inject into the server.')
        ),
        ADMIN_USER: properties.Schema(
            properties.Schema.STRING,
            _('Name of the administrative user to use on the server.'),
            default='ec2-user'
        ),
        AVAILABILITY_ZONE: properties.Schema(
            properties.Schema.STRING,
            _('Name of the availability zone for server placement.')
        ),
        SECURITY_GROUPS: properties.Schema(
            properties.Schema.LIST,
            _('List of security group names or IDs.')
        ),
        NETWORKS: properties.Schema(
            properties.Schema.LIST,
            _('An ordered list of nics to be added to this server, with '
              'information about connected networks, fixed ips, port etc.'),
            schema=properties.Schema(
                properties.Schema.MAP,
                schema={
                    NETWORK_UUID: properties.Schema(
                        properties.Schema.STRING,
                        _('ID of network to create a port on.')
                    ),
                    NETWORK_FIXED_IP: properties.Schema(
                        properties.Schema.STRING,
                        _('Fixed IP address to specify for the port '
                          'created on the requested network.')
                    ),
                    NETWORK_PORT: properties.Schema(
                        properties.Schema.STRING,
                        _('ID of an existing port to associate with this '
                          'server.')
                    ),
                },
            )
        ),
        SCHEDULER_HINTS: properties.Schema(
            properties.Schema.MAP,
            _('Arbitrary key-value pairs specified by the client to help '
              'boot a server.')
        ),
        METADATA: properties.Schema(
            properties.Schema.MAP,
            _('Arbitrary key/value metadata to store for this server. A '
              'maximum of five entries is allowed, and both keys and values '
              'must be 255 characters or less.'),
            update_allowed=True
        ),
        USER_DATA_FORMAT: properties.Schema(
            properties.Schema.STRING,
            _('How the user_data should be formatted for the server. For '
              'HEAT_CFNTOOLS, the user_data is bundled as part of the '
              'heat-cfntools cloud-init boot configuration data. For RAW, '
              'the user_data is passed to Nova unmodified.'),
            default='HEAT_CFNTOOLS',
            constraints=[
                constraints.AllowedValues(['HEAT_CFNTOOLS', 'RAW']),
            ]
        ),
        USER_DATA: properties.Schema(
            properties.Schema.STRING,
            _('User data script to be executed by cloud-init.')
        ),
        RESERVATION_ID: properties.Schema(
            properties.Schema.STRING,
            _('A UUID for the set of servers being requested.')
        ),
        CONFIG_DRIVE: properties.Schema(
            properties.Schema.STRING,
            _('value for config drive either boolean, or volume-id.')
        ),
        DISK_CONFIG: properties.Schema(
            properties.Schema.STRING,
            _('Control how the disk is partitioned when the server is '
              'created.'),
            constraints=[
                constraints.AllowedValues(['AUTO', 'MANUAL']),
            ]
        ),
    }

    attributes_schema = {
        'show': _('A dict of all server details as returned by the API.'),
        'addresses': _('A dict of all network addresses as returned by '
                       'the API.'),
        'networks': _('A dict of assigned network addresses of the form: '
                      '{"public": [ip1, ip2...], "private": [ip3, ip4]}.'),
        'first_address': _('Convenience attribute to fetch the first '
                           'assigned network address, or an '
                           'empty string if nothing has been assigned '
                           'at this time. Result may not be predictable '
                           'if the server has addresses from more than one '
                           'network.'),
        'instance_name': _('AWS compatible instance name.'),
        'accessIPv4': _('The manually assigned alternative public IPv4 '
                        'address of the server.'),
        'accessIPv6': _('The manually assigned alternative public IPv6 '
                        'address of the server.'),
    }

    update_allowed_keys = ('Metadata', 'Properties')

    # Server host name limit to 53 characters by due to typical default
    # linux HOST_NAME_MAX of 64, minus the .novalocal appended to the name
    physical_resource_name_limit = 53

    def __init__(self, name, json_snippet, stack):
        super(Server, self).__init__(name, json_snippet, stack)

    def physical_resource_name(self):
        name = self.properties.get(self.NAME)
        if name:
            return name

        return super(Server, self).physical_resource_name()

    def handle_create(self):
        security_groups = self.properties.get(self.SECURITY_GROUPS, [])

        user_data_format = self.properties.get(self.USER_DATA_FORMAT)
        userdata = nova_utils.build_userdata(
            self,
            self.properties.get(self.USER_DATA, ''),
            instance_user=self.properties[self.ADMIN_USER],
            user_data_format=user_data_format)

        flavor = self.properties[self.FLAVOR]
        availability_zone = self.properties[self.AVAILABILITY_ZONE]

        key_name = self.properties[self.KEY_NAME]
        if key_name:
            # confirm keypair exists
            nova_utils.get_keypair(self.nova(), key_name)

        image = self.properties.get(self.IMAGE)
        if image:
            image = nova_utils.get_image_id(self.nova(), image)

        flavor_id = nova_utils.get_flavor_id(self.nova(), flavor)
        instance_meta = self.properties.get(self.METADATA)
        scheduler_hints = self.properties.get(self.SCHEDULER_HINTS)
        nics = self._build_nics(self.properties.get(self.NETWORKS))
        block_device_mapping = self._build_block_device_mapping(
            self.properties.get(self.BLOCK_DEVICE_MAPPING))
        reservation_id = self.properties.get(self.RESERVATION_ID)
        config_drive = self.properties.get(self.CONFIG_DRIVE)
        disk_config = self.properties.get(self.DISK_CONFIG)

        server = None
        try:
            server = self.nova().servers.create(
                name=self.physical_resource_name(),
                image=image,
                flavor=flavor_id,
                key_name=key_name,
                security_groups=security_groups,
                userdata=userdata,
                meta=instance_meta,
                scheduler_hints=scheduler_hints,
                nics=nics,
                availability_zone=availability_zone,
                block_device_mapping=block_device_mapping,
                reservation_id=reservation_id,
                config_drive=config_drive,
                disk_config=disk_config)
        finally:
            # Avoid a race condition where the thread could be cancelled
            # before the ID is stored
            if server is not None:
                self.resource_id_set(server.id)

        return server

    def check_create_complete(self, server):
        return self._check_active(server)

    def _check_active(self, server):

        if server.status != 'ACTIVE':
            server.get()

        # Some clouds append extra (STATUS) strings to the status
        short_server_status = server.status.split('(')[0]
        if short_server_status in nova_utils.deferred_server_statuses:
            return False
        elif server.status == 'ACTIVE':
            return True
        elif server.status == 'ERROR':
            exc = exception.Error(_('Creation of server %s failed.') %
                                  server.name)
            raise exc
        else:
            exc = exception.Error(_('Creation of server %(server)s failed '
                                    'with unknown status: %(status)s') %
                                  dict(server=server.name,
                                       status=server.status))
            raise exc

    @classmethod
    def _build_block_device_mapping(cls, bdm):
        if not bdm:
            return None
        bdm_dict = {}
        for mapping in bdm:
            mapping_parts = []
            snapshot_id = mapping.get(cls.BLOCK_DEVICE_MAPPING_SNAPSHOT_ID)
            if snapshot_id:
                mapping_parts.append(snapshot_id)
                mapping_parts.append('snap')
            else:
                volume_id = mapping.get(cls.BLOCK_DEVICE_MAPPING_VOLUME_ID)
                mapping_parts.append(volume_id)
                mapping_parts.append('')

            volume_size = mapping.get(cls.BLOCK_DEVICE_MAPPING_VOLUME_SIZE)
            delete = mapping.get(cls.BLOCK_DEVICE_MAPPING_DELETE_ON_TERM)
            if volume_size or delete:
                mapping_parts.append(volume_size or '0')
            if delete:
                mapping_parts.append(str(delete))

            device_name = mapping.get(cls.BLOCK_DEVICE_MAPPING_DEVICE_NAME)
            bdm_dict[device_name] = ':'.join(mapping_parts)

        return bdm_dict

    @classmethod
    def _build_nics(cls, networks):
        if not networks:
            return None

        nics = []

        for net_data in networks:
            nic_info = {}
            if net_data.get(cls.NETWORK_UUID):
                nic_info['net-id'] = net_data[cls.NETWORK_UUID]
            if net_data.get(cls.NETWORK_FIXED_IP):
                nic_info['v4-fixed-ip'] = net_data[cls.NETWORK_FIXED_IP]
            if net_data.get(cls.NETWORK_PORT):
                nic_info['port-id'] = net_data[cls.NETWORK_PORT]
            nics.append(nic_info)
        return nics

    def _resolve_attribute(self, name):
        if name == 'first_address':
            return nova_utils.server_to_ipaddress(
                self.nova(), self.resource_id) or ''
        server = self.nova().servers.get(self.resource_id)
        if name == 'addresses':
            return server.addresses
        if name == 'networks':
            return server.networks
        if name == 'instance_name':
            return server._info.get('OS-EXT-SRV-ATTR:instance_name')
        if name == 'accessIPv4':
            return server.accessIPv4
        if name == 'accessIPv6':
            return server.accessIPv6
        if name == 'show':
            return server._info

    def handle_update(self, json_snippet, tmpl_diff, prop_diff):
        if 'Metadata' in tmpl_diff:
            self.metadata = tmpl_diff['Metadata']

        checkers = []
        server = None

        if 'metadata' in prop_diff:
            server = self.nova().servers.get(self.resource_id)
            nova_utils.meta_update(self.nova(),
                                   server,
                                   prop_diff['metadata'])

        if 'flavor' in prop_diff:

            flavor_update_policy = (
                prop_diff.get('flavor_update_policy') or
                self.properties.get(self.FLAVOR_UPDATE_POLICY))

            if flavor_update_policy == 'REPLACE':
                raise resource.UpdateReplace(self.name)

            flavor = prop_diff['flavor']
            flavor_id = nova_utils.get_flavor_id(self.nova(), flavor)
            if not server:
                server = self.nova().servers.get(self.resource_id)
            checker = scheduler.TaskRunner(nova_utils.resize, server, flavor,
                                           flavor_id)
            checkers.append(checker)

        if 'image' in prop_diff:
            image_update_policy = (
                prop_diff.get('image_update_policy') or
                self.properties.get(self.IMAGE_UPDATE_POLICY))
            if image_update_policy == 'REPLACE':
                raise resource.UpdateReplace(self.name)
            image = prop_diff['image']
            image_id = nova_utils.get_image_id(self.nova(), image)
            if not server:
                server = self.nova().servers.get(self.resource_id)
            checker = scheduler.TaskRunner(nova_utils.rebuild, server,
                                           image_id)
            checkers.append(checker)

        # Optimization: make sure the first task is started before
        # check_update_complete.
        if checkers:
            checkers[0].start()

        return checkers

    def check_update_complete(self, checkers):
        '''Push all checkers to completion in list order.'''
        for checker in checkers:
            if not checker.started():
                checker.start()
            if not checker.step():
                return False
        return True

    def metadata_update(self, new_metadata=None):
        '''
        Refresh the metadata if new_metadata is None
        '''
        if new_metadata is None:
            self.metadata = self.parsed_template('Metadata')

    def validate(self):
        '''
        Validate any of the provided params
        '''
        super(Server, self).validate()

        # check validity of key
        key_name = self.properties.get(self.KEY_NAME, None)
        if key_name:
            nova_utils.get_keypair(self.nova(), key_name)

        # either volume_id or snapshot_id needs to be specified, but not both
        # for block device mapping.
        bdm = self.properties.get(self.BLOCK_DEVICE_MAPPING) or []
        bootable_vol = False
        for mapping in bdm:
            device_name = mapping[self.BLOCK_DEVICE_MAPPING_DEVICE_NAME]
            if device_name == 'vda':
                bootable_vol = True

            volume_id = mapping.get(self.BLOCK_DEVICE_MAPPING_VOLUME_ID)
            snapshot_id = mapping.get(self.BLOCK_DEVICE_MAPPING_SNAPSHOT_ID)
            if volume_id and snapshot_id:
                raise exception.ResourcePropertyConflict('volume_id',
                                                         'snapshot_id')
            if not volume_id and not snapshot_id:
                msg = _('Either volume_id or snapshot_id must be specified for'
                        ' device mapping %s') % device_name
                raise exception.StackValidationFailed(message=msg)

        # make sure the image exists if specified.
        image = self.properties.get(self.IMAGE, None)
        if image:
            nova_utils.get_image_id(self.nova(), image)
        elif not image and not bootable_vol:
            msg = _('Neither image nor bootable volume is specified for'
                    ' instance %s') % self.name
            raise exception.StackValidationFailed(message=msg)

    def handle_delete(self):
        '''
        Delete a server, blocking until it is disposed by OpenStack
        '''
        if self.resource_id is None:
            return

        try:
            server = self.nova().servers.get(self.resource_id)
        except clients.novaclient.exceptions.NotFound:
            pass
        else:
            delete = scheduler.TaskRunner(nova_utils.delete_server, server)
            delete(wait_time=0.2)

        self.resource_id_set(None)

    def handle_suspend(self):
        '''
        Suspend a server - note we do not wait for the SUSPENDED state,
        this is polled for by check_suspend_complete in a similar way to the
        create logic so we can take advantage of coroutines
        '''
        if self.resource_id is None:
            raise exception.Error(_('Cannot suspend %s, resource_id not set') %
                                  self.name)

        try:
            server = self.nova().servers.get(self.resource_id)
        except clients.novaclient.exceptions.NotFound:
            raise exception.NotFound(_('Failed to find server %s') %
                                     self.resource_id)
        else:
            logger.debug(_('suspending server %s') % self.resource_id)
            # We want the server.suspend to happen after the volume
            # detachement has finished, so pass both tasks and the server
            suspend_runner = scheduler.TaskRunner(server.suspend)
            return server, suspend_runner

    def check_suspend_complete(self, cookie):
        server, suspend_runner = cookie

        if not suspend_runner.started():
            suspend_runner.start()

        if suspend_runner.done():
            if server.status == 'SUSPENDED':
                return True

            server.get()
            logger.debug(_('%(name)s check_suspend_complete status '
                         '= %(status)s') % {
                         'name': self.name, 'status': server.status})
            if server.status in list(nova_utils.deferred_server_statuses +
                                     ['ACTIVE']):
                return server.status == 'SUSPENDED'
            else:
                exc = exception.Error(_('Suspend of server %(server)s failed '
                                        'with unknown status: %(status)s') %
                                      dict(server=server.name,
                                           status=server.status))
                raise exc

    def handle_resume(self):
        '''
        Resume a server - note we do not wait for the ACTIVE state,
        this is polled for by check_resume_complete in a similar way to the
        create logic so we can take advantage of coroutines
        '''
        if self.resource_id is None:
            raise exception.Error(_('Cannot resume %s, resource_id not set') %
                                  self.name)

        try:
            server = self.nova().servers.get(self.resource_id)
        except clients.novaclient.exceptions.NotFound:
            raise exception.NotFound(_('Failed to find server %s') %
                                     self.resource_id)
        else:
            logger.debug(_('resuming server %s') % self.resource_id)
            server.resume()
            return server

    def check_resume_complete(self, server):
        return self._check_active(server)


def resource_mapping():
    return {
        'OS::Nova::Server': Server,
    }
