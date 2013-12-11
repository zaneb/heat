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

from heat.common import exception
from heat.engine import clients
from heat.engine.resources.neutron import neutron
from heat.engine import properties
from heat.engine import scheduler

if clients.neutronclient is not None:
    from neutronclient.common.exceptions import NeutronClientException
    from neutronclient.neutron import v2_0 as neutronV20

from heat.openstack.common import log as logging

logger = logging.getLogger(__name__)


class Router(neutron.NeutronResource):
    PROPERTIES = (
        NAME, VALUE_SPECS, ADMIN_STATE_UP,
    ) = (
        'name', 'value_specs', 'admin_state_up',
    )

    properties_schema = {
        NAME: properties.Schema(
            properties.Schema.STRING,
            update_allowed=True
        ),
        VALUE_SPECS: properties.Schema(
            properties.Schema.MAP,
            default={},
            update_allowed=True
        ),
        ADMIN_STATE_UP: properties.Schema(
            properties.Schema.BOOLEAN,
            default=True,
            update_allowed=True
        ),
    }

    attributes_schema = {
        "status": _("The status of the router."),
        "external_gateway_info": _("Gateway network for the router."),
        "name": _("Friendly name of the router."),
        "admin_state_up": _("Administrative state of the router."),
        "tenant_id": _("Tenant owning the router."),
        "show": _("All attributes."),
    }

    update_allowed_keys = ('Properties',)

    def handle_create(self):
        props = self.prepare_properties(
            self.properties,
            self.physical_resource_name())
        router = self.neutron().create_router({'router': props})['router']
        self.resource_id_set(router['id'])

    def _show_resource(self):
        return self.neutron().show_router(
            self.resource_id)['router']

    def check_create_complete(self, *args):
        attributes = self._show_resource()
        return self.is_built(attributes)

    def handle_delete(self):
        client = self.neutron()
        try:
            client.delete_router(self.resource_id)
        except NeutronClientException as ex:
            if ex.status_code != 404:
                raise ex
        else:
            return scheduler.TaskRunner(self._confirm_delete)()

    def handle_update(self, json_snippet, tmpl_diff, prop_diff):
        props = self.prepare_update_properties(json_snippet)
        self.neutron().update_router(
            self.resource_id, {'router': props})


class RouterInterface(neutron.NeutronResource):
    PROPERTIES = (
        ROUTER_ID, SUBNET_ID, PORT_ID,
    ) = (
        'router_id', 'subnet_id', 'port_id',
    )

    properties_schema = {
        ROUTER_ID: properties.Schema(
            properties.Schema.STRING,
            _('The router id.'),
            required=True
        ),
        SUBNET_ID: properties.Schema(
            properties.Schema.STRING,
            _('The subnet id, either subnet_id or port_id should be '
              'specified.')
        ),
        PORT_ID: properties.Schema(
            properties.Schema.STRING,
            _('The port id, either subnet_id or port_id should be specified.')
        ),
    }

    def validate(self):
        '''
        Validate any of the provided params
        '''
        super(RouterInterface, self).validate()
        subnet_id = self.properties.get(self.SUBNET_ID)
        port_id = self.properties.get(self.PORT_ID)
        if subnet_id and port_id:
            raise exception.ResourcePropertyConflict('subnet_id', 'port_id')
        if not subnet_id and not port_id:
            msg = 'Either subnet_id or port_id must be specified.'
            raise exception.StackValidationFailed(message=msg)

    def handle_create(self):
        router_id = self.properties.get(self.ROUTER_ID)
        key = 'subnet_id'
        value = self.properties.get(key)
        if not value:
            key = 'port_id'
            value = self.properties.get(key)
        self.neutron().add_interface_router(
            router_id,
            {key: value})
        self.resource_id_set('%s:%s=%s' % (router_id, key, value))

    def handle_delete(self):
        if not self.resource_id:
            return
        client = self.neutron()
        tokens = self.resource_id.replace('=', ':').split(':')
        if len(tokens) == 2:    # compatible with old data
            tokens.insert(1, 'subnet_id')
        (router_id, key, value) = tokens
        try:
            client.remove_interface_router(
                router_id,
                {key: value})
        except NeutronClientException as ex:
            if ex.status_code != 404:
                raise ex


class RouterGateway(neutron.NeutronResource):
    PROPERTIES = (
        ROUTER_ID, NETWORK_ID,
    ) = (
        'router_id', 'network_id',
    )

    properties_schema = {
        ROUTER_ID: properties.Schema(
            properties.Schema.STRING,
            required=True
        ),
        NETWORK_ID: properties.Schema(
            properties.Schema.STRING,
            required=True
        ),
    }

    def add_dependencies(self, deps):
        super(RouterGateway, self).add_dependencies(deps)
        for resource in self.stack.itervalues():
            # depend on any RouterInterface in this template with the same
            # router_id as this router_id
            if (resource.has_interface('OS::Neutron::RouterInterface') and
                resource.properties.get('router_id') ==
                    self.properties.get(self.ROUTER_ID)):
                        deps += (self, resource)
            # depend on any subnet in this template with the same network_id
            # as this network_id, as the gateway implicitly creates a port
            # on that subnet
            elif (resource.has_interface('OS::Neutron::Subnet') and
                  resource.properties.get('network_id') ==
                    self.properties.get(self.NETWORK_ID)):
                        deps += (self, resource)

    def handle_create(self):
        router_id = self.properties.get(self.ROUTER_ID)
        network_id = neutronV20.find_resourceid_by_name_or_id(
            self.neutron(),
            'network',
            self.properties.get(self.NETWORK_ID))
        self.neutron().add_gateway_router(
            router_id,
            {'network_id': network_id})
        self.resource_id_set('%s:%s' % (router_id, network_id))

    def handle_delete(self):
        if not self.resource_id:
            return
        client = self.neutron()
        (router_id, network_id) = self.resource_id.split(':')
        try:
            client.remove_gateway_router(router_id)
        except NeutronClientException as ex:
            if ex.status_code != 404:
                raise ex


def resource_mapping():
    if clients.neutronclient is None:
        return {}

    return {
        'OS::Neutron::Router': Router,
        'OS::Neutron::RouterInterface': RouterInterface,
        'OS::Neutron::RouterGateway': RouterGateway,
    }
