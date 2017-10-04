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

"""
Module to define placeholder values for parameters, attributes, and refids.

Parameter values are not (necessarily) known when validating a template.
Resource attribute and reference ID values are not known until after the
resource is created. Placeholder values allow templates to be validated prior
to creating a Stack and its resources, including detecting type mismatches.
"""


import six

from oslo_utils import encodeutils


def UnknownPlaceholder():
    return None


class Placeholder(object):
    """Base class for placeholder objects."""

    def __init__(self, constraints=None):
        self.constraints = constraints or []

    def __repr__(self):
        fields = {
            'class': type(self).__name__,
            'value': super(Placeholder, self).__repr__(),
            'constr': repr(self.constraints),
        }
        return '%(class)s(%(value)s, constraints=%(constr)s)' % fields


class StringPlaceholder(Placeholder, six.text_type):
    """Placeholder class for string values."""

    def __new__(cls, default, *args, **kwargs):
        default = encodeutils.safe_decode(default)
        return six.text_type.__new__(cls, default)

    def __init__(self, default, constraints=None):
        super(StringPlaceholder, self).__init__(constraints)


def typed_StringPlaceholder(default, custom_constraint=None):
    """Return a placeholder string with a known custom constraint.

    Convenience function for defining a string placeholder for a resource
    reference ID (i.e. the result of the {get_resource: } intrinsic function)
    or other string with a known type. The optional custom constraint name
    indicates the expected type of the final value, e.g. 'nova.server'.
    """
    constrs = None
    if custom_constraint is not None:
        from heat.engine import constraints
        constrs = [constraints.CustomConstraint(custom_constraint)]
    return StringPlaceholder(default, constraints=constrs)


class IntPlaceholder(Placeholder, int):
    """Placeholder class for integer values."""

    def __new__(cls, default, *args, **kwargs):
        if not isinstance(default, int):
            raise TypeError("Invalid value for integer placeholder %r" %
                            default)
        return super(IntPlaceholder, cls).__new__(cls, default)

    def __init__(self, default, constraints=None):
        super(IntPlaceholder, self).__init__(constraints)


class FloatPlaceholder(Placeholder, float):
    """Placeholder class for floating point values."""

    def __new__(cls, default, *args, **kwargs):
        return super(FloatPlaceholder, cls).__new__(cls, default)

    def __init__(self, default, constraints=None):
        super(FloatPlaceholder, self).__init__(constraints)


class DictPlaceholder(Placeholder, dict):
    """Placeholder class for dict values.

    If the structure of the dict is known, the contents may be provided (this
    may include further placeholder values). Getting an item always returns the
    default value (i.e. KeyError is never raised), but for all other operations
    the dict will appear to contain only those keys explicitly specified.
    """
    def __init__(self, default=UnknownPlaceholder(), contents=None,
                 constraints=None):
        dict.__init__(self, contents or {})
        super(DictPlaceholder, self).__init__(constraints)
        self._default = default

    def __getitem__(self, key):
        try:
            return super(DictPlaceholder, self).__getitem__(key)
        except KeyError:
            if callable(self._default):
                return self._default(key)
            return self._default


class ListPlaceholder(Placeholder, list):
    """Placeholder class for list values.

    Getting an item always returns the default value (i.e. IndexError is never
    raised), but for all other operations the list will appear to be empty.
    """
    def __init__(self, default=UnknownPlaceholder(), constraints=None):
        super(ListPlaceholder, self).__init__(constraints)
        self._default = default

    def __getitem__(self, index):
        return self._default

    def __len__(self):
        return 0
