# vim: tabstop=4 shiftwidth=4 softtabstop=4

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


import unittest
import mox

from nose.plugins.attrib import attr

from heat.common import context
from heat.common import exception
from heat.common import template_format
from heat.engine import parser
from heat.engine.resources import stack as nested_stack
from heat.common import urlfetch


@attr(tag=['unit', 'resource'])
@attr(speed='fast')
class NestedStackTest(unittest.TestCase):
    test_template = '''
HeatTemplateFormatVersion: '2012-12-12'
Resources:
  the_nested:
    Type: AWS::CloudFormation::Stack
    Properties:
      TemplateURL: https://localhost/the.template
      Parameters:
        KeyName: foo
'''

    nested_template = '''
HeatTemplateFormatVersion: '2012-12-12'
Outputs:
  Foo:
    Value: bar
'''

    def setUp(self):
        self.m = mox.Mox()
        self.m.StubOutWithMock(urlfetch, 'get')

    def tearDown(self):
        self.m.UnsetStubs()
        print "NestedStackTest teardown complete"

    def create_stack(self, template):
        t = template_format.parse(template)
        stack = self.parse_stack(t)
        stack.create()
        self.assertEqual(stack.state, stack.CREATE_COMPLETE)
        return stack

    def parse_stack(self, t):
        ctx = context.RequestContext.from_dict({
            'tenant': 'test_tenant',
            'tenant_id': 'aaaa',
            'username': 'test_username',
            'password': 'password',
            'auth_url': 'http://localhost:5000/v2.0'})
        stack_name = 'test_stack'
        tmpl = parser.Template(t)
        params = parser.Parameters(stack_name, tmpl, {})
        stack = parser.Stack(ctx, stack_name, tmpl, params)
        stack.store()
        return stack

    def test_nested_stack(self):
        urlfetch.get('https://localhost/the.template').AndReturn(
            self.nested_template)
        self.m.ReplayAll()

        stack = self.create_stack(self.test_template)
        resource = stack['the_nested']
        self.assertTrue(resource.FnGetRefId().startswith(
            'arn:openstack:heat::aaaa:stacks/test_stack.the_nested/'))

        self.assertEqual(nested_stack.NestedStack.UPDATE_REPLACE,
                         resource.handle_update({}))

        self.assertEqual('bar', resource.FnGetAtt('Outputs.Foo'))
        self.assertRaises(
            exception.InvalidTemplateAttribute, resource.FnGetAtt, 'Foo')

        resource.delete()
        self.assertTrue(resource.FnGetRefId().startswith(
            'arn:openstack:heat::aaaa:stacks/test_stack.the_nested/'))

        self.m.VerifyAll()
