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

import urllib
import urlparse
import json

from oslo.config import cfg

from keystoneclient.contrib.ec2.utils import Ec2Signer

from heat.common import exception
from heat.common import identifier
from heat.engine import resource
from heat.engine import scheduler

from heat.openstack.common import log as logging

logger = logging.getLogger(__name__)


class WaitConditionHandle(resource.Resource):
    '''
    the main point of this class is to :
    have no dependancies (so the instance can reference it)
    generate a unique url (to be returned in the refernce)
    then the cfn-signal will use this url to post to and
    WaitCondition will poll it to see if has been written to.
    '''
    properties_schema = {}

    def __init__(self, name, json_snippet, stack):
        super(WaitConditionHandle, self).__init__(name, json_snippet, stack)

    def _sign_url(self, credentials, path):
        """
        Create properly formatted and pre-signed URL using supplied credentials
        See http://docs.amazonwebservices.com/AWSECommerceService/latest/DG/
            rest-signature.html
        Also see boto/auth.py::QuerySignatureV2AuthHandler
        """
        host_url = urlparse.urlparse(cfg.CONF.heat_waitcondition_server_url)
        # Note the WSGI spec apparently means that the webob request we end up
        # prcessing in the CFN API (ec2token.py) has an unquoted path, so we
        # need to calculate the signature with the path component unquoted, but
        # ensure the actual URL contains the quoted version...
        unquoted_path = urllib.unquote(host_url.path + path)
        request = {'host': host_url.netloc.lower(),
                   'verb': 'PUT',
                   'path': unquoted_path,
                   'params': {'SignatureMethod': 'HmacSHA256',
                              'SignatureVersion': '2',
                              'AWSAccessKeyId': credentials.access,
                              'Timestamp':
                              self.created_time.strftime("%Y-%m-%dT%H:%M:%SZ")
                              }}
        # Sign the request
        signer = Ec2Signer(credentials.secret)
        request['params']['Signature'] = signer.generate(request)

        qs = urllib.urlencode(request['params'])
        url = "%s%s?%s" % (cfg.CONF.heat_waitcondition_server_url.lower(),
                           path, qs)
        return url

    def handle_create(self):
        # Create a keystone user so we can create a signed URL via FnGetRefId
        user_id = self.keystone().create_stack_user(
            self.physical_resource_name())
        kp = self.keystone().get_ec2_keypair(user_id)
        if not kp:
            raise exception.Error("Error creating ec2 keypair for user %s" %
                                  user_id)
        else:
            self.resource_id_set(user_id)

    def handle_delete(self):
        if self.resource_id is None:
            return
        self.keystone().delete_stack_user(self.resource_id)

    def handle_update(self, json_snippet):
        return self.UPDATE_REPLACE

    def FnGetRefId(self):
        '''
        Override the default resource FnGetRefId so we return the signed URL
        '''
        if self.resource_id:
            urlpath = self.identifier().arn_url_path()
            ec2_creds = self.keystone().get_ec2_keypair(self.resource_id)
            signed_url = self._sign_url(ec2_creds, urlpath)
            return unicode(signed_url)
        else:
            return unicode(self.name)

    def _metadata_format_ok(self, metadata):
        """
        Check the format of the provided metadata is as expected.
        metadata must use the following format:
        {
            "Status" : "Status (must be SUCCESS or FAILURE)"
            "UniqueId" : "Some ID, should be unique for Count>1",
            "Data" : "Arbitrary Data",
            "Reason" : "Reason String"
        }
        """
        expected_keys = ['Data', 'Reason', 'Status', 'UniqueId']
        if sorted(metadata.keys()) == expected_keys:
            return metadata['Status'] in WAIT_STATUSES

    def metadata_update(self, new_metadata=None):
        '''
        Validate and update the resource metadata
        '''
        if new_metadata is None:
            return

        if self._metadata_format_ok(new_metadata):
            rsrc_metadata = self.metadata
            if new_metadata['UniqueId'] in rsrc_metadata:
                logger.warning("Overwriting Metadata item for UniqueId %s!" %
                               new_metadata['UniqueId'])
            safe_metadata = {}
            for k in ('Data', 'Reason', 'Status'):
                safe_metadata[k] = new_metadata[k]
            # Note we can't update self.metadata directly, as it
            # is a Metadata descriptor object which only supports get/set
            rsrc_metadata.update({new_metadata['UniqueId']: safe_metadata})
            self.metadata = rsrc_metadata
        else:
            logger.error("Metadata failed validation for %s" % self.name)
            raise ValueError("Metadata format invalid")

    def get_status(self):
        '''
        Return a list of the Status values for the handle signals
        '''
        return [self.metadata[s]['Status']
                for s in self.metadata]

    def get_status_reason(self, status):
        '''
        Return the reason associated with a particular status
        If there is more than one handle signal matching the specified status
        then return a semicolon delimited string containing all reasons
        '''
        return ';'.join([self.metadata[s]['Reason']
                        for s in self.metadata
                        if self.metadata[s]['Status'] == status])


WAIT_STATUSES = (
    STATUS_FAILURE,
    STATUS_SUCCESS,
) = (
    'FAILURE',
    'SUCCESS',
)


class WaitConditionFailure(Exception):
    def __init__(self, wait_condition, handle):
        reasons = handle.get_status_reason(STATUS_FAILURE)
        super(WaitConditionFailure, self).__init__(reasons)


class WaitConditionTimeout(Exception):
    def __init__(self, wait_condition, handle):
        reasons = handle.get_status_reason(STATUS_SUCCESS)
        message = '%d of %d received' % (len(reasons), wait_condition.count)
        if reasons:
            message += ' - %s' % reasons

        super(WaitConditionTimeout, self).__init__(message)


class WaitCondition(resource.Resource):
    properties_schema = {'Handle': {'Type': 'String',
                                    'Required': True},
                         'Timeout': {'Type': 'Number',
                                     'Required': True,
                                     'MinValue': '1'},
                         'Count': {'Type': 'Number',
                                   'MinValue': '1'}}

    def __init__(self, name, json_snippet, stack):
        super(WaitCondition, self).__init__(name, json_snippet, stack)

        self.count = int(self.t['Properties'].get('Count', '1'))

    def _validate_handle_url(self):
        handle_url = self.properties['Handle']
        handle_id = identifier.ResourceIdentifier.from_arn_url(handle_url)
        if handle_id.tenant != self.stack.context.tenant_id:
            raise ValueError("WaitCondition invalid Handle tenant %s" %
                             handle_id.tenant)
        if handle_id.stack_name != self.stack.name:
            raise ValueError("WaitCondition invalid Handle stack %s" %
                             handle_id.stack_name)
        if handle_id.stack_id != self.stack.id:
            raise ValueError("WaitCondition invalid Handle stack %s" %
                             handle_id.stack_id)
        if handle_id.resource_name not in self.stack:
            raise ValueError("WaitCondition invalid Handle %s" %
                             handle_id.resource_name)
        if not isinstance(self.stack[handle_id.resource_name],
                          WaitConditionHandle):
            raise ValueError("WaitCondition invalid Handle %s" %
                             handle_id.resource_name)

    def _get_handle_resource_name(self):
        handle_url = self.properties['Handle']
        handle_id = identifier.ResourceIdentifier.from_arn_url(handle_url)
        return handle_id.resource_name

    def _wait(self, handle):
        while True:
            try:
                yield
            except scheduler.Timeout:
                timeout = WaitConditionTimeout(self, handle)
                logger.info('%s Timed out (%s)' % (str(self), str(timeout)))
                raise timeout

            handle_status = handle.get_status()

            if any(s != STATUS_SUCCESS for s in handle_status):
                failure = WaitConditionFailure(self, handle)
                logger.info('%s Failed (%s)' % (str(self), str(failure)))
                raise failure

            if len(handle_status) >= self.count:
                logger.info("%s Succeeded" % str(self))
                return

    def handle_create(self):
        self._validate_handle_url()
        handle_res_name = self._get_handle_resource_name()
        handle = self.stack[handle_res_name]
        self.resource_id_set(handle_res_name)

        runner = scheduler.TaskRunner(self._wait, handle)
        runner.start(timeout=float(self.properties['Timeout']))
        return runner

    def check_active(self, runner):
        return runner.step()

    def handle_update(self, json_snippet):
        return self.UPDATE_REPLACE

    def handle_delete(self):
        if self.resource_id is None:
            return

        handle = self.stack[self.resource_id]
        handle.metadata = {}

    def FnGetAtt(self, key):
        res = {}
        handle_res_name = self._get_handle_resource_name()
        handle = self.stack[handle_res_name]
        if key == 'Data':
            meta = handle.metadata
            # Note, can't use a dict generator on python 2.6, hence:
            res = dict([(k, meta[k]['Data']) for k in meta])
        else:
            raise exception.InvalidTemplateAttribute(resource=self.name,
                                                     key=key)

        logger.debug('%s.GetAtt(%s) == %s' % (self.name, key, res))
        return unicode(json.dumps(res))


def resource_mapping():
    return {
        'AWS::CloudFormation::WaitCondition': WaitCondition,
        'AWS::CloudFormation::WaitConditionHandle': WaitConditionHandle,
    }
