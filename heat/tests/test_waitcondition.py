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


import mox
import time
import uuid
import datetime
import json

from nose.plugins.attrib import attr
from oslo.config import cfg
import unittest

from heat.tests import fakes
from heat.tests.utils import stack_delete_after

import heat.db as db_api
from heat.common import template_format
from heat.common import identifier
from heat.engine import parser
from heat.engine import scheduler
from heat.engine.resources import wait_condition as wc
from heat.common import context

test_template_waitcondition = '''
{
  "AWSTemplateFormatVersion" : "2010-09-09",
  "Description" : "Just a WaitCondition.",
  "Parameters" : {},
  "Resources" : {
    "WaitHandle" : {
      "Type" : "AWS::CloudFormation::WaitConditionHandle"
    },
    "WaitForTheHandle" : {
      "Type" : "AWS::CloudFormation::WaitCondition",
      "Properties" : {
        "Handle" : {"Ref" : "WaitHandle"},
        "Timeout" : "5"
      }
    }
  }
}
'''

test_template_wc_count = '''
{
  "AWSTemplateFormatVersion" : "2010-09-09",
  "Description" : "Just a WaitCondition.",
  "Parameters" : {},
  "Resources" : {
    "WaitHandle" : {
      "Type" : "AWS::CloudFormation::WaitConditionHandle"
    },
    "WaitForTheHandle" : {
      "Type" : "AWS::CloudFormation::WaitCondition",
      "Properties" : {
        "Handle" : {"Ref" : "WaitHandle"},
        "Timeout" : "5",
        "Count" : "3"
      }
    }
  }
}
'''


@attr(tag=['unit', 'resource', 'WaitCondition'])
@attr(speed='slow')
class WaitConditionTest(unittest.TestCase):
    def setUp(self):
        self.m = mox.Mox()
        self.m.StubOutWithMock(wc.WaitConditionHandle,
                               'get_status')
        self.m.StubOutWithMock(scheduler.TaskRunner, '_sleep')

        cfg.CONF.set_default('heat_waitcondition_server_url',
                             'http://127.0.0.1:8000/v1/waitcondition')

        self.fc = fakes.FakeKeystoneClient()

    def tearDown(self):
        self.m.UnsetStubs()

    # Note tests creating a stack should be decorated with @stack_delete_after
    # to ensure the stack is properly cleaned up
    def create_stack(self, stack_name='test_stack',
                     template=test_template_waitcondition, params={},
                     stub=True):
        temp = template_format.parse(template)
        template = parser.Template(temp)
        parameters = parser.Parameters(stack_name, template, params)
        ctx = context.get_admin_context()
        ctx.tenant_id = 'test_tenant'
        stack = parser.Stack(ctx, stack_name, template, parameters,
                             disable_rollback=True)

        self.stack_id = stack.store()

        if stub:
            scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)

            self.m.StubOutWithMock(wc.WaitConditionHandle, 'keystone')
            wc.WaitConditionHandle.keystone().MultipleTimes().AndReturn(
                self.fc)

            id = identifier.ResourceIdentifier('test_tenant', stack.name,
                                               stack.id, '', 'WaitHandle')
            self.m.StubOutWithMock(wc.WaitConditionHandle, 'identifier')
            wc.WaitConditionHandle.identifier().MultipleTimes().AndReturn(id)

        return stack

    @stack_delete_after
    def test_post_success_to_handle(self):
        self.stack = self.create_stack()
        wc.WaitConditionHandle.get_status().AndReturn([])
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
        wc.WaitConditionHandle.get_status().AndReturn([])
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
        wc.WaitConditionHandle.get_status().AndReturn(['SUCCESS'])

        self.m.ReplayAll()

        self.stack.create()

        resource = self.stack.resources['WaitForTheHandle']
        self.assertEqual(resource.state,
                         'CREATE_COMPLETE')

        r = db_api.resource_get_by_name_and_stack(None, 'WaitHandle',
                                                  self.stack.id)
        self.assertEqual(r.name, 'WaitHandle')
        self.m.VerifyAll()

    @stack_delete_after
    def test_post_failure_to_handle(self):
        self.stack = self.create_stack()
        wc.WaitConditionHandle.get_status().AndReturn([])
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
        wc.WaitConditionHandle.get_status().AndReturn([])
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
        wc.WaitConditionHandle.get_status().AndReturn(['FAILURE'])

        self.m.ReplayAll()

        self.stack.create()

        resource = self.stack.resources['WaitForTheHandle']
        self.assertEqual(resource.state, resource.CREATE_FAILED)
        reason = resource.state_description
        self.assertTrue(reason.startswith('WaitConditionFailure:'))

        r = db_api.resource_get_by_name_and_stack(None, 'WaitHandle',
                                                  self.stack.id)
        self.assertEqual(r.name, 'WaitHandle')
        self.m.VerifyAll()

    @stack_delete_after
    def test_post_success_to_handle_count(self):
        self.stack = self.create_stack(template=test_template_wc_count)
        wc.WaitConditionHandle.get_status().AndReturn([])
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
        wc.WaitConditionHandle.get_status().AndReturn(['SUCCESS'])
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
        wc.WaitConditionHandle.get_status().AndReturn(['SUCCESS', 'SUCCESS'])
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
        wc.WaitConditionHandle.get_status().AndReturn(['SUCCESS', 'SUCCESS',
                                                       'SUCCESS'])

        self.m.ReplayAll()

        self.stack.create()

        resource = self.stack.resources['WaitForTheHandle']
        self.assertEqual(resource.state,
                         'CREATE_COMPLETE')

        r = db_api.resource_get_by_name_and_stack(None, 'WaitHandle',
                                                  self.stack.id)
        self.assertEqual(r.name, 'WaitHandle')
        self.m.VerifyAll()

    @stack_delete_after
    def test_post_failure_to_handle_count(self):
        self.stack = self.create_stack(template=test_template_wc_count)
        wc.WaitConditionHandle.get_status().AndReturn([])
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
        wc.WaitConditionHandle.get_status().AndReturn(['SUCCESS'])
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
        wc.WaitConditionHandle.get_status().AndReturn(['SUCCESS', 'FAILURE'])

        self.m.ReplayAll()

        self.stack.create()

        resource = self.stack.resources['WaitForTheHandle']
        self.assertEqual(resource.state, resource.CREATE_FAILED)
        reason = resource.state_description
        self.assertTrue(reason.startswith('WaitConditionFailure:'))

        r = db_api.resource_get_by_name_and_stack(None, 'WaitHandle',
                                                  self.stack.id)
        self.assertEqual(r.name, 'WaitHandle')
        self.m.VerifyAll()

    @stack_delete_after
    def test_timeout(self):
        st = time.time()

        self.stack = self.create_stack()

        self.m.StubOutWithMock(scheduler, 'wallclock')

        scheduler.wallclock().AndReturn(st)
        scheduler.wallclock().AndReturn(st + 0.001)
        scheduler.wallclock().AndReturn(st + 0.1)
        wc.WaitConditionHandle.get_status().AndReturn([])
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
        scheduler.wallclock().AndReturn(st + 4.1)
        wc.WaitConditionHandle.get_status().AndReturn([])
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
        scheduler.wallclock().AndReturn(st + 5.1)

        self.m.ReplayAll()

        self.stack.create()

        resource = self.stack.resources['WaitForTheHandle']

        self.assertEqual(resource.state, resource.CREATE_FAILED)
        reason = resource.state_description
        self.assertTrue(reason.startswith('WaitConditionTimeout:'))

        self.assertEqual(wc.WaitCondition.UPDATE_REPLACE,
                         resource.handle_update({}))
        self.m.VerifyAll()

    @stack_delete_after
    def test_FnGetAtt(self):
        self.stack = self.create_stack()
        wc.WaitConditionHandle.get_status().AndReturn(['SUCCESS'])

        self.m.ReplayAll()
        self.stack.create()

        resource = self.stack.resources['WaitForTheHandle']
        self.assertEqual(resource.state, 'CREATE_COMPLETE')

        wc_att = resource.FnGetAtt('Data')
        self.assertEqual(wc_att, unicode({}))

        handle = self.stack.resources['WaitHandle']
        self.assertEqual(handle.state, 'CREATE_COMPLETE')

        test_metadata = {'Data': 'foo', 'Reason': 'bar',
                         'Status': 'SUCCESS', 'UniqueId': '123'}
        handle.metadata_update(new_metadata=test_metadata)
        wc_att = resource.FnGetAtt('Data')
        self.assertEqual(wc_att, '{"123": "foo"}')

        test_metadata = {'Data': 'dog', 'Reason': 'cat',
                         'Status': 'SUCCESS', 'UniqueId': '456'}
        handle.metadata_update(new_metadata=test_metadata)
        wc_att = resource.FnGetAtt('Data')
        self.assertEqual(wc_att, u'{"123": "foo", "456": "dog"}')
        self.m.VerifyAll()

    @stack_delete_after
    def test_validate_handle_url_bad_stackid(self):
        # Stub out the stack ID so we have a known value
        stack_id = 'STACKABCD1234'
        self.m.StubOutWithMock(uuid, 'uuid4')
        uuid.uuid4().AndReturn(stack_id)
        self.m.ReplayAll()

        t = json.loads(test_template_waitcondition)
        badhandle = ("http://127.0.0.1:8000/v1/waitcondition/" +
                     "arn%3Aopenstack%3Aheat%3A%3Atest_tenant" +
                     "%3Astacks%2Ftest_stack%2F" +
                     "bad1" +
                     "%2Fresources%2FWaitHandle")
        t['Resources']['WaitForTheHandle']['Properties']['Handle'] = badhandle
        self.stack = self.create_stack(template=json.dumps(t), stub=False)
        self.m.ReplayAll()

        resource = self.stack.resources['WaitForTheHandle']
        self.assertRaises(ValueError, resource.handle_create)

        self.m.VerifyAll()

    @stack_delete_after
    def test_validate_handle_url_bad_stackname(self):
        # Stub out the stack ID so we have a known value
        stack_id = 'STACKABCD1234'
        self.m.StubOutWithMock(uuid, 'uuid4')
        uuid.uuid4().AndReturn(stack_id)
        self.m.ReplayAll()

        t = json.loads(test_template_waitcondition)
        badhandle = ("http://127.0.0.1:8000/v1/waitcondition/" +
                     "arn%3Aopenstack%3Aheat%3A%3Atest_tenant" +
                     "%3Astacks%2FBAD_stack%2F" +
                     stack_id + "%2Fresources%2FWaitHandle")
        t['Resources']['WaitForTheHandle']['Properties']['Handle'] = badhandle
        self.stack = self.create_stack(template=json.dumps(t), stub=False)

        resource = self.stack.resources['WaitForTheHandle']
        self.assertRaises(ValueError, resource.handle_create)

        self.m.VerifyAll()

    @stack_delete_after
    def test_validate_handle_url_bad_tenant(self):
        # Stub out the stack ID so we have a known value
        stack_id = 'STACKABCD1234'
        self.m.StubOutWithMock(uuid, 'uuid4')
        uuid.uuid4().AndReturn(stack_id)
        self.m.ReplayAll()

        t = json.loads(test_template_waitcondition)
        badhandle = ("http://127.0.0.1:8000/v1/waitcondition/" +
                     "arn%3Aopenstack%3Aheat%3A%3ABAD_tenant" +
                     "%3Astacks%2Ftest_stack%2F" +
                     stack_id + "%2Fresources%2FWaitHandle")
        t['Resources']['WaitForTheHandle']['Properties']['Handle'] = badhandle
        self.stack = self.create_stack(template=json.dumps(t), stub=False)

        resource = self.stack.resources['WaitForTheHandle']
        self.assertRaises(ValueError, resource.handle_create)

        self.m.VerifyAll()

    @stack_delete_after
    def test_validate_handle_url_bad_resource(self):
        # Stub out the stack ID so we have a known value
        stack_id = 'STACKABCD1234'
        self.m.StubOutWithMock(uuid, 'uuid4')
        uuid.uuid4().AndReturn(stack_id)
        self.m.ReplayAll()

        t = json.loads(test_template_waitcondition)
        badhandle = ("http://127.0.0.1:8000/v1/waitcondition/" +
                     "arn%3Aopenstack%3Aheat%3A%3Atest_tenant" +
                     "%3Astacks%2Ftest_stack%2F" +
                     stack_id + "%2Fresources%2FBADHandle")
        t['Resources']['WaitForTheHandle']['Properties']['Handle'] = badhandle
        self.stack = self.create_stack(template=json.dumps(t), stub=False)

        resource = self.stack.resources['WaitForTheHandle']
        self.assertRaises(ValueError, resource.handle_create)

        self.m.VerifyAll()

    @stack_delete_after
    def test_validate_handle_url_bad_resource_type(self):
        # Stub out the stack ID so we have a known value
        stack_id = 'STACKABCD1234'
        self.m.StubOutWithMock(uuid, 'uuid4')
        uuid.uuid4().AndReturn(stack_id)
        self.m.ReplayAll()

        t = json.loads(test_template_waitcondition)
        badhandle = ("http://127.0.0.1:8000/v1/waitcondition/" +
                     "arn%3Aopenstack%3Aheat%3A%3Atest_tenant" +
                     "%3Astacks%2Ftest_stack%2F" +
                     stack_id + "%2Fresources%2FWaitForTheHandle")
        t['Resources']['WaitForTheHandle']['Properties']['Handle'] = badhandle
        self.stack = self.create_stack(template=json.dumps(t), stub=False)

        resource = self.stack.resources['WaitForTheHandle']
        self.assertRaises(ValueError, resource.handle_create)

        self.m.VerifyAll()


@attr(tag=['unit', 'resource', 'WaitConditionHandle'])
@attr(speed='fast')
class WaitConditionHandleTest(unittest.TestCase):
    def setUp(self):
        self.m = mox.Mox()
        cfg.CONF.set_default('heat_waitcondition_server_url',
                             'http://127.0.0.1:8000/v1/waitcondition')

        self.fc = fakes.FakeKeystoneClient()
        self.stack = self.create_stack()

    def tearDown(self):
        self.m.UnsetStubs()

    def create_stack(self, stack_name='test_stack2', params={}):
        temp = template_format.parse(test_template_waitcondition)
        template = parser.Template(temp)
        parameters = parser.Parameters(stack_name, template, params)
        ctx = context.get_admin_context()
        ctx.tenant_id = 'test_tenant'
        stack = parser.Stack(ctx, stack_name, template, parameters,
                             disable_rollback=True)
        # Stub out the UUID for this test, so we can get an expected signature
        self.m.StubOutWithMock(uuid, 'uuid4')
        uuid.uuid4().AndReturn('STACKABCD1234')
        self.m.ReplayAll()
        stack.store()

        self.m.StubOutWithMock(scheduler.TaskRunner, '_sleep')
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)

        # Stub waitcondition status so all goes CREATE_COMPLETE
        self.m.StubOutWithMock(wc.WaitConditionHandle, 'get_status')
        wc.WaitConditionHandle.get_status().AndReturn(['SUCCESS'])

        # Stub keystone() with fake client
        self.m.StubOutWithMock(wc.WaitConditionHandle, 'keystone')
        wc.WaitConditionHandle.keystone().MultipleTimes().AndReturn(self.fc)

        id = identifier.ResourceIdentifier('test_tenant', stack.name,
                                           stack.id, '', 'WaitHandle')
        self.m.StubOutWithMock(wc.WaitConditionHandle, 'identifier')
        wc.WaitConditionHandle.identifier().MultipleTimes().AndReturn(id)

        self.m.ReplayAll()
        stack.create()

        return stack

    @stack_delete_after
    def test_handle(self):
        created_time = datetime.datetime(2012, 11, 29, 13, 49, 37)

        resource = self.stack.resources['WaitHandle']
        resource.created_time = created_time
        self.assertEqual(resource.state, 'CREATE_COMPLETE')

        expected_url = "".join([
            'http://127.0.0.1:8000/v1/waitcondition/',
            'arn%3Aopenstack%3Aheat%3A%3Atest_tenant%3Astacks%2F',
            'test_stack2%2FSTACKABCD1234%2Fresources%2F',
            'WaitHandle?',
            'Timestamp=2012-11-29T13%3A49%3A37Z&',
            'SignatureMethod=HmacSHA256&',
            'AWSAccessKeyId=4567&',
            'SignatureVersion=2&',
            'Signature=',
            'ePyTwmC%2F1kSigeo%2Fha7kP8Avvb45G9Y7WOQWe4F%2BnXM%3D'])

        self.assertEqual(expected_url, resource.FnGetRefId())

        self.assertEqual(resource.UPDATE_REPLACE, resource.handle_update({}))
        self.m.VerifyAll()

    @stack_delete_after
    def test_metadata_update(self):
        resource = self.stack.resources['WaitHandle']
        self.assertEqual(resource.state, 'CREATE_COMPLETE')

        test_metadata = {'Data': 'foo', 'Reason': 'bar',
                         'Status': 'SUCCESS', 'UniqueId': '123'}
        resource.metadata_update(new_metadata=test_metadata)
        handle_metadata = {u'123': {u'Data': u'foo',
                                    u'Reason': u'bar',
                                    u'Status': u'SUCCESS'}}
        self.assertEqual(resource.metadata, handle_metadata)
        self.m.VerifyAll()

    @stack_delete_after
    def test_metadata_update_invalid(self):
        resource = self.stack.resources['WaitHandle']
        self.assertEqual(resource.state, 'CREATE_COMPLETE')

        # metadata_update should raise a ValueError if the metadata
        # is missing any of the expected keys
        err_metadata = {'Data': 'foo', 'Status': 'SUCCESS', 'UniqueId': '123'}
        self.assertRaises(ValueError, resource.metadata_update,
                          new_metadata=err_metadata)

        err_metadata = {'Data': 'foo', 'Reason': 'bar', 'UniqueId': '1234'}
        self.assertRaises(ValueError, resource.metadata_update,
                          new_metadata=err_metadata)

        err_metadata = {'Data': 'foo', 'Reason': 'bar', 'UniqueId': '1234'}
        self.assertRaises(ValueError, resource.metadata_update,
                          new_metadata=err_metadata)

        err_metadata = {'data': 'foo', 'reason': 'bar',
                        'status': 'SUCCESS', 'uniqueid': '1234'}
        self.assertRaises(ValueError, resource.metadata_update,
                          new_metadata=err_metadata)

        # Also any Status other than SUCCESS or FAILURE should be rejected
        err_metadata = {'Data': 'foo', 'Reason': 'bar',
                        'Status': 'UCCESS', 'UniqueId': '123'}
        self.assertRaises(ValueError, resource.metadata_update,
                          new_metadata=err_metadata)
        err_metadata = {'Data': 'foo', 'Reason': 'bar',
                        'Status': 'wibble', 'UniqueId': '123'}
        self.assertRaises(ValueError, resource.metadata_update,
                          new_metadata=err_metadata)
        err_metadata = {'Data': 'foo', 'Reason': 'bar',
                        'Status': 'success', 'UniqueId': '123'}
        self.assertRaises(ValueError, resource.metadata_update,
                          new_metadata=err_metadata)
        err_metadata = {'Data': 'foo', 'Reason': 'bar',
                        'Status': 'FAIL', 'UniqueId': '123'}
        self.assertRaises(ValueError, resource.metadata_update,
                          new_metadata=err_metadata)
        self.m.VerifyAll()

    @stack_delete_after
    def test_get_status(self):
        resource = self.stack.resources['WaitHandle']
        self.assertEqual(resource.state, 'CREATE_COMPLETE')

        # UnsetStubs, don't want get_status stubbed anymore..
        self.m.VerifyAll()
        self.m.UnsetStubs()

        self.assertEqual(resource.get_status(), [])

        test_metadata = {'Data': 'foo', 'Reason': 'bar',
                         'Status': 'SUCCESS', 'UniqueId': '123'}
        resource.metadata_update(new_metadata=test_metadata)
        self.assertEqual(resource.get_status(), ['SUCCESS'])

        test_metadata = {'Data': 'foo', 'Reason': 'bar',
                         'Status': 'SUCCESS', 'UniqueId': '456'}
        resource.metadata_update(new_metadata=test_metadata)
        self.assertEqual(resource.get_status(), ['SUCCESS', 'SUCCESS'])

        # re-stub keystone() with fake client or stack delete fails
        self.m.StubOutWithMock(wc.WaitConditionHandle, 'keystone')
        wc.WaitConditionHandle.keystone().MultipleTimes().AndReturn(self.fc)
        self.m.ReplayAll()

    @stack_delete_after
    def test_get_status_reason(self):
        resource = self.stack.resources['WaitHandle']
        self.assertEqual(resource.state, 'CREATE_COMPLETE')

        test_metadata = {'Data': 'foo', 'Reason': 'bar',
                         'Status': 'SUCCESS', 'UniqueId': '123'}
        resource.metadata_update(new_metadata=test_metadata)
        self.assertEqual(resource.get_status_reason('SUCCESS'), 'bar')

        test_metadata = {'Data': 'dog', 'Reason': 'cat',
                         'Status': 'SUCCESS', 'UniqueId': '456'}
        resource.metadata_update(new_metadata=test_metadata)
        self.assertEqual(resource.get_status_reason('SUCCESS'), 'bar;cat')

        test_metadata = {'Data': 'boo', 'Reason': 'hoo',
                         'Status': 'FAILURE', 'UniqueId': '789'}
        resource.metadata_update(new_metadata=test_metadata)
        self.assertEqual(resource.get_status_reason('FAILURE'), 'hoo')
        self.m.VerifyAll()
