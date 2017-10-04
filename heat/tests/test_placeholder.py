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

import six

from heat.engine import constraints
from heat.engine import placeholder
from heat.tests import common


class StringPlaceholderTest(common.HeatTestCase):
    def test_is_string(self):
        sp = placeholder.StringPlaceholder('foo')

        self.assertIsInstance(sp, six.text_type)
        self.assertIsInstance(sp, placeholder.Placeholder)

    def test_default(self):
        sp = placeholder.StringPlaceholder('bar')

        self.assertEqual('bar', sp)

    def test_default_bytes(self):
        sp = placeholder.StringPlaceholder(b'bar')

        self.assertEqual('bar', sp)

    def test_default_unicode(self):
        sp = placeholder.StringPlaceholder('\u2603')

        self.assertEqual('\u2603', sp)

    def test_default_unicode_bytes(self):
        sp = placeholder.StringPlaceholder('\u2603'.encode('utf-8'))

        self.assertEqual('\u2603', sp)

    def test_no_constraints(self):
        sp = placeholder.StringPlaceholder('blarg')

        self.assertEqual([], sp.constraints)

    def test_constraints(self):
        sp = placeholder.StringPlaceholder('wibble', ['a', 'b', 'c'])

        self.assertEqual(['a', 'b', 'c'], sp.constraints)

    def test_unique(self):
        s1 = placeholder.StringPlaceholder('baz', [1, 2, 3])
        s2 = placeholder.StringPlaceholder('baz', [4, 5, 6])

        self.assertIsNot(s1, s2)
        self.assertEqual(s1, s2)
        self.assertEqual([1, 2, 3], s1.constraints)
        self.assertEqual([4, 5, 6], s2.constraints)

    def test_typed(self):
        rp = placeholder.typed_StringPlaceholder('myresource')

        self.assertEqual('myresource', rp)

    def test_typed_no_constraint(self):
        rp = placeholder.typed_StringPlaceholder('myresource', None)

        self.assertEqual('myresource', rp)
        self.assertEqual([], rp.constraints)

    def test_typed_constraint(self):
        rp = placeholder.typed_StringPlaceholder('myresource', 'nova.keypair')

        self.assertEqual('myresource', rp)
        self.assertIsInstance(rp.constraints, collections.Sequence)
        self.assertEqual(1, len(rp.constraints))
        constraint = rp.constraints[0]
        self.assertIsInstance(constraint, constraints.CustomConstraint)
        self.assertEqual('nova.keypair', constraint.name)

    def test_repr(self):
        rp = placeholder.typed_StringPlaceholder('myresource', 'nova.keypair')
        self.assertEqual("StringPlaceholder('myresource', "
                         "constraints=[CustomConstraint('nova.keypair')])",
                         repr(rp))


class IntPlaceholderTest(common.HeatTestCase):
    def test_is_int(self):
        ip = placeholder.IntPlaceholder(0)

        self.assertIsInstance(ip, int)
        self.assertIsInstance(ip, placeholder.Placeholder)

    def test_default(self):
        ip = placeholder.IntPlaceholder(42)

        self.assertEqual(42, ip)

    def test_no_constraints(self):
        ip = placeholder.IntPlaceholder(0)

        self.assertEqual([], ip.constraints)

    def test_constraints(self):
        ip = placeholder.IntPlaceholder(0, ['a', 'b', 'c'])

        self.assertEqual(['a', 'b', 'c'], ip.constraints)

    def test_unique(self):
        i1 = placeholder.IntPlaceholder(1, [1, 2, 3])
        i2 = placeholder.IntPlaceholder(1, [4, 5, 6])

        self.assertIsNot(i1, i2)
        self.assertEqual(i1, i2)
        self.assertEqual([1, 2, 3], i1.constraints)
        self.assertEqual([4, 5, 6], i2.constraints)

    def test_repr(self):
        ip = placeholder.IntPlaceholder(0)
        self.assertEqual("IntPlaceholder(0, constraints=[])", repr(ip))


class FloatPlaceholderTest(common.HeatTestCase):
    def test_is_float(self):
        fp = placeholder.FloatPlaceholder(0)

        self.assertIsInstance(fp, float)
        self.assertIsInstance(fp, placeholder.Placeholder)

    def test_default(self):
        fp = placeholder.FloatPlaceholder(42.5)

        self.assertEqual(42.5, fp)

    def test_no_constraints(self):
        fp = placeholder.FloatPlaceholder(0)

        self.assertEqual([], fp.constraints)

    def test_constraints(self):
        fp = placeholder.FloatPlaceholder(0, ['a', 'b', 'c'])

        self.assertEqual(['a', 'b', 'c'], fp.constraints)

    def test_unique(self):
        f1 = placeholder.FloatPlaceholder(0, [1, 2, 3])
        f2 = placeholder.FloatPlaceholder(0, [4, 5, 6])

        self.assertIsNot(f1, f2)
        self.assertEqual(f1, f2)
        self.assertEqual([1, 2, 3], f1.constraints)
        self.assertEqual([4, 5, 6], f2.constraints)

    def test_repr(self):
        fp = placeholder.FloatPlaceholder(0)
        self.assertEqual("FloatPlaceholder(0.0, constraints=[])", repr(fp))


class DictPlaceholderTest(common.HeatTestCase):
    def test_is_dict(self):
        dp = placeholder.DictPlaceholder()

        self.assertIsInstance(dp, dict)
        self.assertIsInstance(dp, collections.Mapping)
        self.assertIsInstance(dp, placeholder.Placeholder)

    def test_no_constraints(self):
        dp = placeholder.DictPlaceholder()

        self.assertEqual([], dp.constraints)

    def test_constraints(self):
        dp = placeholder.DictPlaceholder(constraints=['a', 'b', 'c'])

        self.assertEqual(['a', 'b', 'c'], dp.constraints)

    def test_no_default_no_contents(self):
        dp = placeholder.DictPlaceholder()

        self.assertFalse(dp)
        self.assertEqual(0, len(dp))
        self.assertEqual([], list(iter(dp)))
        self.assertEqual([], list(dp.values()))
        self.assertIsNone(dp['foo'])

    def test_default_no_contents(self):
        dp = placeholder.DictPlaceholder('foo')

        self.assertFalse(dp)
        self.assertEqual(0, len(dp))
        self.assertEqual([], list(iter(dp)))
        self.assertEqual([], list(dp.values()))
        self.assertEqual('foo', dp['bar'])
        self.assertNotIsInstance(dp['bar'], placeholder.StringPlaceholder)

    def test_no_default_contents(self):
        dp = placeholder.DictPlaceholder(contents={'blarg': 'wibble'})

        self.assertEqual(1, len(dp))
        self.assertEqual(['blarg'], list(iter(dp)))
        self.assertEqual(['wibble'], list(dp.values()))
        self.assertIsNone(dp['foo'])
        self.assertEqual('wibble', dp['blarg'])

    def test_default_contents(self):
        dp = placeholder.DictPlaceholder('baz', {'blarg': 'wibble'})

        self.assertEqual(1, len(dp))
        self.assertEqual(['blarg'], list(iter(dp)))
        self.assertEqual(['wibble'], list(dp.values()))
        self.assertEqual('baz', dp['foo'])
        self.assertEqual('wibble', dp['blarg'])

    def test_default_function(self):
        dp = placeholder.DictPlaceholder(placeholder.StringPlaceholder)

        bar = dp['bar']
        self.assertEqual('bar', bar)
        self.assertIsInstance(bar, placeholder.StringPlaceholder)

    def test_repr(self):
        dp = placeholder.DictPlaceholder('baz', {'foo': 'bar'})
        self.assertEqual("DictPlaceholder({'foo': 'bar'}, constraints=[])",
                         repr(dp))


class ListPlaceholderTest(common.HeatTestCase):
    def test_is_list(self):
        lp = placeholder.ListPlaceholder()

        self.assertIsInstance(lp, list)
        self.assertIsInstance(lp, collections.Sequence)
        self.assertIsInstance(lp, placeholder.Placeholder)

    def test_no_constraints(self):
        lp = placeholder.ListPlaceholder()

        self.assertEqual([], lp.constraints)

    def test_constraints(self):
        lp = placeholder.ListPlaceholder(constraints=['a', 'b', 'c'])

        self.assertEqual(['a', 'b', 'c'], lp.constraints)

    def test_no_default(self):
        lp = placeholder.ListPlaceholder()

        self.assertEqual(0, len(lp))
        self.assertEqual([], list(iter(lp)))
        self.assertIsNone(lp[42])

    def test_default(self):
        lp = placeholder.ListPlaceholder('foo')

        self.assertEqual(0, len(lp))
        self.assertEqual([], list(iter(lp)))
        self.assertEqual('foo', lp[99])

    def test_repr(self):
        lp = placeholder.ListPlaceholder('foo')
        self.assertEqual("ListPlaceholder([], constraints=[])", repr(lp))
