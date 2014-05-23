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

import collections

from heat.engine import function
from heat.engine import parameters
from heat.engine import rsrc_defn
from heat.engine import template


_RESOURCE_KEYS = (
    RES_TYPE, RES_PROPERTIES, RES_METADATA, RES_DEPENDS_ON,
    RES_DELETION_POLICY, RES_UPDATE_POLICY, RES_DESCRIPTION,
) = (
    'Type', 'Properties', 'Metadata', 'DependsOn',
    'DeletionPolicy', 'UpdatePolicy', 'Description',
)


class CfnTemplate(template.Template):
    '''A stack template.'''

    SECTIONS = (VERSION, ALTERNATE_VERSION, DESCRIPTION, MAPPINGS,
                PARAMETERS, RESOURCES, OUTPUTS) = \
               ('AWSTemplateFormatVersion', 'HeatTemplateFormatVersion',
                'Description', 'Mappings', 'Parameters', 'Resources', 'Outputs'
                )

    SECTIONS_NO_DIRECT_ACCESS = set([PARAMETERS, VERSION, ALTERNATE_VERSION])

    def __getitem__(self, section):
        '''Get the relevant section in the template.'''
        if section not in self.SECTIONS:
            raise KeyError(_('"%s" is not a valid template section') % section)
        if section in self.SECTIONS_NO_DIRECT_ACCESS:
            raise KeyError(
                _('Section %s can not be accessed directly.') % section)

        if section == self.DESCRIPTION:
            default = 'No description'
        else:
            default = {}

        return self.t.get(section, default)

    def param_schemata(self):
        params = self.t.get(self.PARAMETERS, {}).iteritems()
        return dict((name, parameters.Schema.from_dict(name, schema))
                    for name, schema in params)

    def parameters(self, stack_identifier, user_params, validate_value=True,
                   context=None):
        return parameters.Parameters(stack_identifier, self,
                                     user_params=user_params,
                                     validate_value=validate_value,
                                     context=context)

    def resource_definitions(self, stack):
        def rsrc_defn_item(name, snippet):
            data = self.parse(stack, snippet)

            def get_check_type(key, valid_types, typename, default=None):
                if key in data:
                    field = data[key]
                    if not isinstance(field, valid_types):
                        args = {'name': name, 'key': key, 'typename': typename}
                        msg = _('Resource %(name)s %(key)s type'
                                'must be %(typename)s') % args
                        raise TypeError(msg)
                    return field
                else:
                    return default

            resource_type = get_check_type(RES_TYPE, basestring, 'string')
            if resource_type is None:
                args = {'name': name, 'type_key': RES_TYPE}
                msg = _('Resource %(name)s is missing "%(type_key)s"') % args
                raise KeyError(msg)

            properties = get_check_type(RES_PROPERTIES,
                                        (collections.Mapping,
                                         function.Function),
                                        'object')

            metadata = get_check_type(RES_METADATA,
                                      (collections.Mapping,
                                       function.Function),
                                      'object')

            depends = get_check_type(RES_DEPENDS_ON,
                                     collections.Sequence,
                                     'list or string',
                                     default=[])
            if isinstance(depends, basestring):
                depends = [depends]

            deletion_policy = get_check_type(RES_DELETION_POLICY,
                                             basestring,
                                             'string')

            update_policy = get_check_type(RES_UPDATE_POLICY,
                                           (collections.Mapping,
                                            function.Function),
                                           'object')

            description = get_check_type(RES_DESCRIPTION,
                                         basestring,
                                         'string',
                                         default='')

            defn = rsrc_defn.ResourceDefinition(name, resource_type,
                                                properties, metadata,
                                                depends,
                                                deletion_policy,
                                                update_policy,
                                                description=description)
            return name, defn

        resources = self.t.get(self.RESOURCES, {}).items()
        return dict(rsrc_defn_item(name, data) for name, data in resources)


def template_mapping():
    return {
        ('HeatTemplateFormatVersion', '2012-12-12'): CfnTemplate,
        ('AWSTemplateFormatVersion', '2010-09-09'): CfnTemplate,
    }
