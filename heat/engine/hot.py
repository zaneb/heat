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

from heat.engine import template
from heat.engine import parameters
from heat.openstack.common.gettextutils import _
from heat.openstack.common import log as logging


logger = logging.getLogger(__name__)

SECTIONS = (VERSION, DESCRIPTION, PARAMETER_GROUPS, PARAMETERS,
            RESOURCES, OUTPUTS, UNDEFINED) = \
           ('heat_template_version', 'description', 'parameter_groups',
            'parameters', 'resources', 'outputs', '__undefined__')

PARAM_CONSTRAINTS = (CONSTRAINTS, DESCRIPTION, LENGTH, RANGE,
                     MIN, MAX, ALLOWED_VALUES, ALLOWED_PATTERN) = \
                    ('constraints', 'description', 'length', 'range',
                     'min', 'max', 'allowed_values', 'allowed_pattern')

_CFN_TO_HOT_SECTIONS = {template.VERSION: VERSION,
                        template.DESCRIPTION: DESCRIPTION,
                        template.PARAMETERS: PARAMETERS,
                        template.MAPPINGS: UNDEFINED,
                        template.RESOURCES: RESOURCES,
                        template.OUTPUTS: OUTPUTS}


def snake_to_camel(name):
    return ''.join([t.capitalize() for t in name.split('_')])


class HOTemplate(template.Template):
    """
    A Heat Orchestration Template format stack template.
    """

    def __getitem__(self, section):
        """"Get the relevant section in the template."""
        #first translate from CFN into HOT terminology if necessary
        section = HOTemplate._translate(section, _CFN_TO_HOT_SECTIONS, section)

        if section not in SECTIONS:
            raise KeyError(_('"%s" is not a valid template section') % section)

        if section == VERSION:
            return self.t[section]

        if section == UNDEFINED:
            return {}

        if section == DESCRIPTION:
            default = 'No description'
        else:
            default = {}

        the_section = self.t.get(section, default)

        # In some cases (e.g. parameters), also translate each entry of
        # a section into CFN format (case, naming, etc) so the rest of the
        # engine can cope with it.
        # This is a shortcut for now and might be changed in the future.

        if section == PARAMETERS:
            return self._translate_parameters(the_section)

        if section == RESOURCES:
            return self._translate_resources(the_section)

        if section == OUTPUTS:
            return self._translate_outputs(the_section)

        return the_section

    @staticmethod
    def _translate(value, mapping, default=None):
        if value in mapping:
            return mapping[value]

        return default

    def _translate_parameters(self, parameters):
        """Get the parameters of the template translated into CFN format."""
        params = {}
        for name, attrs in parameters.iteritems():
            param = {}
            for key, val in attrs.iteritems():
                # Do not translate 'constraints' since we want to handle this
                # specifically in HOT and not in common code.
                if key != CONSTRAINTS:
                    key = snake_to_camel(key)
                if key == 'Type':
                    val = snake_to_camel(val)
                elif key == 'Hidden':
                    key = 'NoEcho'
                param[key] = val
            if len(param) > 0:
                params[name] = param
        return params

    def _translate_resources(self, resources):
        """Get the resources of the template translated into CFN format."""
        HOT_TO_CFN_ATTRS = {'type': 'Type',
                            'properties': 'Properties'}

        cfn_resources = {}

        for resource_name, attrs in resources.iteritems():
            cfn_resource = {}

            for attr, attr_value in attrs.iteritems():
                cfn_attr = self._translate(attr, HOT_TO_CFN_ATTRS, attr)
                cfn_resource[cfn_attr] = attr_value

            cfn_resources[resource_name] = cfn_resource

        return cfn_resources

    def _translate_outputs(self, outputs):
        """Get the outputs of the template translated into CFN format."""
        HOT_TO_CFN_ATTRS = {'description': 'Description',
                            'value': 'Value'}

        cfn_outputs = {}

        for output_name, attrs in outputs.iteritems():
            cfn_output = {}

            for attr, attr_value in attrs.iteritems():
                cfn_attr = self._translate(attr, HOT_TO_CFN_ATTRS, attr)
                cfn_output[cfn_attr] = attr_value

            cfn_outputs[output_name] = cfn_output

        return cfn_outputs

    def functions(self):
        return {
            'Fn::FindInMap': template.FindInMap,
            'Fn::GetAZs': template.GetAZs,
            'get_param': template.ParamRef,
            'get_resource': template.ResourceRef,
            'Ref': template.Ref,
            'get_attr': template.GetAtt,
            'Fn::GetAtt': template.GetAtt,
            'Fn::Select': template.Select,
            'Fn::Join': template.Join,
            'Fn::Split': template.Split,
            'str_replace': Replace,
            'Fn::Replace': template.Replace,
            'Fn::Base64': template.Base64,
            'Fn::MemberListToMap': template.MemberListToMap,
            'Fn::ResourceFacade': template.ResourceFacade,
        }

    def param_schemata(self):
        params = self[PARAMETERS].iteritems()
        return dict((name, HOTParamSchema(schema)) for name, schema in params)


class Replace(template.Replace):
    '''
    A function for performing string substitutions.

    Takes the form::

        str_replace:
          template: <key_1> <key_2>
          params:
            <key_1>: <value_1>
            <key_2>: <value_2>
            ...

    And resolves to::

        "<value_1> <value_2>"

    This is implemented using Python's str.replace on each key. The order in
    which replacements are performed is undefined.
    '''

    def _parse_args(self):
        if not isinstance(self.args, collections.Mapping):
            raise TypeError(_('Arguments to "%s" must be a map') %
                            self.fn_name)

        try:
            mapping = self.args['params']
            string = self.args['template']
        except (KeyError, TypeError):
            example = ('''str_replace:
              template: This is var1 template var2
              params:
                var1: a
                var2: string''')
            raise KeyError(_('"str_replace" syntax should be %s') %
                           example)
        else:
            return mapping, string


class HOTParamSchema(parameters.ParamSchema):
    """HOT parameter schema."""

    def do_check(self, name, value, keys):
        # map ParamSchema constraint type to keys used in HOT constraints
        constraint_map = {
            parameters.ALLOWED_PATTERN: [ALLOWED_PATTERN],
            parameters.ALLOWED_VALUES: [ALLOWED_VALUES],
            parameters.MIN_LENGTH: [LENGTH, MIN],
            parameters.MAX_LENGTH: [LENGTH, MAX],
            parameters.MIN_VALUE: [RANGE, MIN],
            parameters.MAX_VALUE: [RANGE, MAX]
        }

        for const_type in keys:
            # get constraint type specific check function
            check = self.check(const_type)
            # get constraint type specific keys in HOT
            const_keys = constraint_map[const_type]

            for constraint in self.get(CONSTRAINTS, []):
                const_descr = constraint.get(DESCRIPTION)

                for const_key in const_keys:
                    if const_key not in constraint:
                        break
                    constraint = constraint[const_key]
                else:
                    check(name, value, constraint, const_descr)
