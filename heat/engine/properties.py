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

from heat.common import exception
from heat.engine import parameters
from heat.engine import constraints as constr

SCHEMA_KEYS = (
    REQUIRED, IMPLEMENTED, DEFAULT, TYPE, SCHEMA,
    ALLOWED_PATTERN, MIN_VALUE, MAX_VALUE, ALLOWED_VALUES,
    MIN_LENGTH, MAX_LENGTH, DESCRIPTION, UPDATE_ALLOWED,
) = (
    'Required', 'Implemented', 'Default', 'Type', 'Schema',
    'AllowedPattern', 'MinValue', 'MaxValue', 'AllowedValues',
    'MinLength', 'MaxLength', 'Description', 'UpdateAllowed',
)


class Schema(constr.Schema):
    """
    Schema class for validating resource properties.

    This class is used for defining schema constraints for resource properties.
    It inherits generic validation features from the base Schema class and add
    processing that is specific to resource properties.
    """

    KEYS = (
        TYPE, DESCRIPTION, DEFAULT, SCHEMA, REQUIRED, CONSTRAINTS,
        UPDATE_ALLOWED
    ) = (
        'type', 'description', 'default', 'schema', 'required', 'constraints',
        'update_allowed'
    )

    def __init__(self, data_type, description=None,
                 default=None, schema=None,
                 required=False, constraints=[],
                 implemented=True,
                 update_allowed=False):
        super(Schema, self).__init__(data_type, description, default,
                                     schema, required, constraints)
        self.implemented = implemented
        self.update_allowed = update_allowed

    @classmethod
    def from_legacy(cls, schema_dict):
        """
        Return a Property Schema object from a legacy schema dictionary.
        """

        # Check for fully-fledged Schema objects
        if isinstance(schema_dict, cls):
            return schema_dict

        unknown = [k for k in schema_dict if k not in SCHEMA_KEYS]
        if unknown:
            raise constr.InvalidSchemaError(_('Unknown key(s) %s') % unknown)

        def constraints():
            def get_num(key):
                val = schema_dict.get(key)
                if val is not None:
                    val = Schema.str_to_num(val)
                return val

            if MIN_VALUE in schema_dict or MAX_VALUE in schema_dict:
                yield constr.Range(get_num(MIN_VALUE), get_num(MAX_VALUE))
            if MIN_LENGTH in schema_dict or MAX_LENGTH in schema_dict:
                yield constr.Length(get_num(MIN_LENGTH), get_num(MAX_LENGTH))
            if ALLOWED_VALUES in schema_dict:
                yield constr.AllowedValues(schema_dict[ALLOWED_VALUES])
            if ALLOWED_PATTERN in schema_dict:
                yield constr.AllowedPattern(schema_dict[ALLOWED_PATTERN])

        try:
            data_type = schema_dict[TYPE]
        except KeyError:
            raise constr.InvalidSchemaError(_('No %s specified') % TYPE)

        if SCHEMA in schema_dict:
            if data_type == Schema.LIST:
                ss = cls.from_legacy(schema_dict[SCHEMA])
            elif data_type == Schema.MAP:
                schema_dicts = schema_dict[SCHEMA].items()
                ss = dict((n, cls.from_legacy(sd)) for n, sd in schema_dicts)
            else:
                raise constr.InvalidSchemaError(_('%(schema)s supplied for '
                                                  ' for %(type)s %(data)s') %
                                                dict(schema=SCHEMA,
                                                     type=TYPE,
                                                     data=data_type))
        else:
            ss = None

        return cls(data_type,
                   description=schema_dict.get(DESCRIPTION),
                   default=schema_dict.get(DEFAULT),
                   schema=ss,
                   required=schema_dict.get(REQUIRED, False),
                   constraints=list(constraints()),
                   implemented=schema_dict.get(IMPLEMENTED, True),
                   update_allowed=schema_dict.get(UPDATE_ALLOWED, False))

    @classmethod
    def from_parameter(cls, param):
        """
        Return a Property Schema corresponding to a parameter.

        Convert a parameter schema from a provider template to a property
        Schema for the corresponding resource facade.
        """
        param_type_map = {
            parameters.STRING: Schema.STRING,
            parameters.NUMBER: Schema.NUMBER,
            parameters.COMMA_DELIMITED_LIST: Schema.LIST,
            parameters.JSON: Schema.MAP
        }

        def get_num(key, context=param):
            val = context.get(key)
            if val is not None:
                val = Schema.str_to_num(val)
            return val

        def constraints():
            desc = param.get(parameters.CONSTRAINT_DESCRIPTION)

            if parameters.MIN_VALUE in param or parameters.MAX_VALUE in param:
                yield constr.Range(get_num(parameters.MIN_VALUE),
                                   get_num(parameters.MAX_VALUE))
            if (parameters.MIN_LENGTH in param or
                    parameters.MAX_LENGTH in param):
                yield constr.Length(get_num(parameters.MIN_LENGTH),
                                    get_num(parameters.MAX_LENGTH))
            if parameters.ALLOWED_VALUES in param:
                yield constr.AllowedValues(param[parameters.ALLOWED_VALUES],
                                           desc)
            if parameters.ALLOWED_PATTERN in param:
                yield constr.AllowedPattern(param[parameters.ALLOWED_PATTERN],
                                            desc)

        import hot

        def constraints_hot():
            constraints = param.get(hot.CONSTRAINTS)
            if constraints is None:
                return

            for constraint in constraints:
                desc = constraint.get(hot.DESCRIPTION)
                if hot.RANGE in constraint:
                    const_def = constraint.get(hot.RANGE)
                    yield constr.Range(get_num(hot.MIN, const_def),
                                       get_num(hot.MAX, const_def), desc)
                if hot.LENGTH in constraint:
                    const_def = constraint.get(hot.LENGTH)
                    yield constr.Length(get_num(hot.MIN, const_def),
                                        get_num(hot.MAX, const_def), desc)
                if hot.ALLOWED_VALUES in constraint:
                    const_def = constraint.get(hot.ALLOWED_VALUES)
                    yield constr.AllowedValues(const_def, desc)
                if hot.ALLOWED_PATTERN in constraint:
                    const_def = constraint.get(hot.ALLOWED_PATTERN)
                    yield constr.AllowedPattern(const_def, desc)

        if isinstance(param, hot.HOTParamSchema):
            constraint_list = list(constraints_hot())
        else:
            constraint_list = list(constraints())

        # make update_allowed true by default on TemplateResources
        # as the template should deal with this.
        return cls(param_type_map.get(param[parameters.TYPE], Schema.MAP),
                   description=param.get(parameters.DESCRIPTION),
                   required=parameters.DEFAULT not in param,
                   constraints=constraint_list,
                   update_allowed=True)

    def __getitem__(self, key):
        if key == self.UPDATE_ALLOWED:
            return self.update_allowed
        else:
            return super(Schema, self).__getitem__(key)

        raise KeyError(key)


def schemata(schema_dicts):
    """
    Return dictionary of Schema objects for given dictionary of schemata.

    The input schemata are converted from the legacy (dictionary-based)
    format to Schema objects where necessary.
    """
    return dict((n, Schema.from_legacy(s)) for n, s in schema_dicts.items())


class Property(object):

    def __init__(self, schema, name=None):
        self.schema = Schema.from_legacy(schema)
        self.name = name

    def required(self):
        return self.schema.required

    def implemented(self):
        return self.schema.implemented

    def update_allowed(self):
        return self.schema.update_allowed

    def has_default(self):
        return self.schema.default is not None

    def default(self):
        return self.schema.default

    def type(self):
        return self.schema.type

    def _validate_integer(self, value):
        if value is None:
            value = self.has_default() and self.default() or 0
        if not isinstance(value, (int, long)):
            raise TypeError(_('value is not an integer'))
        return self._validate_number(value)

    def _validate_number(self, value):
        if value is None:
            value = self.has_default() and self.default() or 0
        return Schema.str_to_num(value)

    def _validate_string(self, value):
        if value is None:
            value = self.has_default() and self.default() or ''
        if not isinstance(value, basestring):
            raise ValueError(_('Value must be a string'))
        return value

    def _validate_children(self, child_values, keys=None):
        if self.schema.schema is not None:
            if keys is None:
                keys = list(self.schema.schema)
            schemata = dict((k, self.schema.schema[k]) for k in keys)
            properties = Properties(schemata, dict(child_values),
                                    parent_name=self.name)
            return ((k, properties[k]) for k in keys)
        else:
            return child_values

    def _validate_map(self, value):
        if value is None:
            value = self.has_default() and self.default() or {}
        if not isinstance(value, collections.Mapping):
            raise TypeError(_('"%s" is not a map') % value)

        return dict(self._validate_children(value.iteritems()))

    def _validate_list(self, value):
        if value is None:
            value = self.has_default() and self.default() or []
        if (not isinstance(value, collections.Sequence) or
                isinstance(value, basestring)):
            raise TypeError(_('"%s" is not a list') % repr(value))

        return [v for i, v in self._validate_children(enumerate(value),
                                                      range(len(value)))]

    def _validate_bool(self, value):
        if value is None:
            value = self.has_default() and self.default() or False
        if isinstance(value, bool):
            return value
        normalised = value.lower()
        if normalised not in ['true', 'false']:
            raise ValueError(_('"%s" is not a valid boolean') % normalised)

        return normalised == 'true'

    def _validate_data_type(self, value):
        t = self.type()
        if t == Schema.STRING:
            return self._validate_string(value)
        elif t == Schema.INTEGER:
            return self._validate_integer(value)
        elif t == Schema.NUMBER:
            return self._validate_number(value)
        elif t == Schema.MAP:
            return self._validate_map(value)
        elif t == Schema.LIST:
            return self._validate_list(value)
        elif t == Schema.BOOLEAN:
            return self._validate_bool(value)

    def validate_data(self, value):
        value = self._validate_data_type(value)
        self.schema.validate_constraints(value)
        return value


class Properties(collections.Mapping):

    def __init__(self, schema, data, resolver=lambda d: d, parent_name=None):
        self.props = dict((k, Property(s, k)) for k, s in schema.items())
        self.resolve = resolver
        self.data = data
        if parent_name is None:
            self.error_prefix = ''
        else:
            self.error_prefix = '%s: ' % parent_name

    @staticmethod
    def schema_from_params(params_snippet):
        """
        Convert a template snippet that defines parameters
        into a properties schema

        :param params_snippet: parameter definition from a template
        :returns: an equivalent properties schema for the specified params
        """
        if params_snippet:
            return dict((n, Schema.from_parameter(p)) for n, p
                        in params_snippet.items())
        return {}

    def validate(self, with_value=True):
        for (key, prop) in self.props.items():
            if with_value:
                try:
                    self[key]
                except ValueError as e:
                    msg = _("Property error : %s") % str(e)
                    raise exception.StackValidationFailed(message=msg)

            # are there unimplemented Properties
            if not prop.implemented() and key in self.data:
                msg = _("Property %s not implemented yet") % key
                raise exception.StackValidationFailed(message=msg)

        for key in self.data:
            if key not in self.props:
                msg = _("Unknown Property %s") % key
                raise exception.StackValidationFailed(message=msg)

    def __getitem__(self, key):
        if key not in self:
            raise KeyError(self.error_prefix + _('Invalid Property %s') % key)

        prop = self.props[key]

        if key in self.data:
            try:
                value = self.resolve(self.data[key])
                return prop.validate_data(value)
            # the resolver function could raise any number of exceptions,
            # so handle this generically
            except Exception as e:
                raise ValueError(self.error_prefix + '%s %s' % (key, str(e)))
        elif prop.has_default():
            return prop.default()
        elif prop.required():
            raise ValueError(self.error_prefix +
                             _('Property %s not assigned') % key)

    def __len__(self):
        return len(self.props)

    def __contains__(self, key):
        return key in self.props

    def __iter__(self):
        return iter(self.props)

    @staticmethod
    def _generate_input(schema, params=None, path=None):
        '''Generate an input based on a path in the schema or property
        defaults.

        :param schema: The schema to generate a parameter or value for.
        :param params: A dict to map a schema to a parameter path.
        :param path: Required if params != None. The params key
            to save the schema at.
        :returns: A Ref to the parameter if path != None and params != None
        :returns: The property default if params == None or path == None
        '''
        if schema.get('Implemented') is False:
            return

        if schema[TYPE] == Schema.LIST:
            params[path] = {parameters.TYPE: parameters.COMMA_DELIMITED_LIST}
            return {'Fn::Split': {'Ref': path}}

        elif schema[TYPE] == Schema.MAP:
            params[path] = {parameters.TYPE: parameters.JSON}
            return {'Ref': path}

        elif params is not None and path is not None:
            for prop in schema.keys():
                if prop not in parameters.PARAMETER_KEYS and prop in schema:
                    del schema[prop]
            params[path] = schema
            return {'Ref': path}
        else:
            prop = Property(schema)
            return prop.has_default() and prop.default() or None

    @staticmethod
    def _schema_to_params_and_props(schema, params=None):
        '''Generates a default template based on the provided schema.
        ::

        ex: input: schema = {'foo': {'Type': 'String'}}, params = {}
            output: {'foo': {'Ref': 'foo'}},
                params = {'foo': {'Type': 'String'}}

        ex: input: schema = {'foo' :{'Type': 'List'}, 'bar': {'Type': 'Map'}}
                    ,params={}
            output: {'foo': {'Fn::Split': {'Ref': 'foo'}},
                     'bar': {'Ref': 'bar'}},
                params = {'foo' : {parameters.TYPE:
                          parameters.COMMA_DELIMITED_LIST},
                          'bar': {parameters.TYPE: parameters.JSON}}

        :param schema: The schema to generate a parameter or value for.
        :param params: A dict to map a schema to a parameter path.
        :returns: A dict of properties resolved for a template's schema
        '''
        properties = {}
        for prop, nested_schema in schema.iteritems():
            properties[prop] = Properties._generate_input(nested_schema,
                                                          params,
                                                          prop)
            #remove not implemented properties
            if properties[prop] is None:
                del properties[prop]
        return properties

    @staticmethod
    def schema_to_parameters_and_properties(schema):
        '''Generates properties with params resolved for a resource's
        properties_schema.

        :param schema: A resource's properties_schema
        :returns: A tuple of params and properties dicts
        '''
        params = {}
        properties = (Properties.
                      _schema_to_params_and_props(schema, params=params))
        return (params, properties)
