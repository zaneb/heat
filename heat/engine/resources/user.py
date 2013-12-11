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
from heat.engine import constraints
from heat.engine import properties
from heat.engine import resource

from heat.openstack.common import log as logging

logger = logging.getLogger(__name__)

#
# We are ignoring Groups as keystone does not support them.
# For now support users and accesskeys,
# We also now support a limited heat-native Policy implementation
#


class User(resource.Resource):
    PROPERTIES = (
        PATH, GROUPS, LOGIN_PROFILE, POLICIES,
    ) = (
        'Path', 'Groups', 'LoginProfile', 'Policies',
    )

    _LOGIN_PROFILE_KEYS = (
        LOGIN_PROFILE_PASSWORD,
    ) = (
        'Password',
    )

    properties_schema = {
        PATH: properties.Schema(
            properties.Schema.STRING,
            _('Not Implemented.')
        ),
        GROUPS: properties.Schema(
            properties.Schema.LIST,
            _('Not Implemented.')
        ),
        LOGIN_PROFILE: properties.Schema(
            properties.Schema.MAP,
            _('A login profile for the user.'),
            schema={
                LOGIN_PROFILE_PASSWORD: properties.Schema(
                    properties.Schema.STRING
                ),
            }
        ),
        POLICIES: properties.Schema(
            properties.Schema.LIST,
            _('Access policies to apply to the user.')
        ),
    }

    def _validate_policies(self, policies):
        for policy in (policies or []):
            # When we support AWS IAM style policies, we will have to accept
            # either a ref to an AWS::IAM::Policy defined in the stack, or
            # and embedded dict describing the policy directly, but for now
            # we only expect this list to contain strings, which must map
            # to an OS::Heat::AccessPolicy in this stack
            # If a non-string (e.g embedded IAM dict policy) is passed, we
            # ignore the policy (don't reject it because we previously ignored
            # and we don't want to break templates which previously worked
            if not isinstance(policy, basestring):
                logger.warning(_("Ignoring policy %s, must be string "
                               "resource name") % policy)
                continue

            try:
                policy_rsrc = self.stack[policy]
            except KeyError:
                logger.error(_("Policy %(policy)s does not exist in stack "
                             "%(stack)s") % {
                             'policy': policy, 'stack': self.stack.name})
                return False

            if not callable(getattr(policy_rsrc, 'access_allowed', None)):
                logger.error(_("Policy %s is not an AccessPolicy resource") %
                             policy)
                return False

        return True

    def handle_create(self):
        passwd = ''
        profile = self.properties[self.LOGIN_PROFILE]
        if profile and self.LOGIN_PROFILE_PASSWORD in profile:
            passwd = profile[self.LOGIN_PROFILE_PASSWORD]

        if self.properties[self.POLICIES]:
            if not self._validate_policies(self.properties[self.POLICIES]):
                raise exception.InvalidTemplateAttribute(resource=self.name,
                                                         key=self.POLICIES)

        uid = self.keystone().create_stack_user(self.physical_resource_name(),
                                                passwd)
        self.resource_id_set(uid)

    def handle_delete(self):
        if self.resource_id is None:
            logger.error(_("Cannot delete User resource before user "
                         "created!"))
            return
        try:
            self.keystone().delete_stack_user(self.resource_id)
        except clients.hkc.kc.exceptions.NotFound:
            pass

    def handle_suspend(self):
        if self.resource_id is None:
            logger.error(_("Cannot suspend User resource before user "
                         "created!"))
            return
        self.keystone().disable_stack_user(self.resource_id)

    def handle_resume(self):
        if self.resource_id is None:
            logger.error(_("Cannot resume User resource before user "
                         "created!"))
            return
        self.keystone().enable_stack_user(self.resource_id)

    def FnGetRefId(self):
        return unicode(self.physical_resource_name())

    def FnGetAtt(self, key):
        #TODO(asalkeld) Implement Arn attribute
        raise exception.InvalidTemplateAttribute(
            resource=self.name, key=key)

    def access_allowed(self, resource_name):
        policies = (self.properties[self.POLICIES] or [])
        for policy in policies:
            if not isinstance(policy, basestring):
                logger.warning(_("Ignoring policy %s, must be string "
                               "resource name") % policy)
                continue
            policy_rsrc = self.stack[policy]
            if not policy_rsrc.access_allowed(resource_name):
                return False
        return True


class AccessKey(resource.Resource):
    PROPERTIES = (
        SERIAL, USER_NAME, STATUS,
    ) = (
        'Serial', 'UserName', 'Status',
    )

    properties_schema = {
        SERIAL: properties.Schema(
            properties.Schema.INTEGER,
            _('Not Implemented.'),
            implemented=False
        ),
        USER_NAME: properties.Schema(
            properties.Schema.STRING,
            _('The name of the user that the new key will belong to.'),
            required=True
        ),
        STATUS: properties.Schema(
            properties.Schema.STRING,
            _('Not Implemented.'),
            constraints=[
                constraints.AllowedValues(['Active', 'Inactive']),
            ],
            implemented=False
        ),
    }

    def __init__(self, name, json_snippet, stack):
        super(AccessKey, self).__init__(name, json_snippet, stack)
        self._secret = None

    def _get_user(self):
        """
        Helper function to derive the keystone userid, which is stored in the
        resource_id of the User associated with this key.  We want to avoid
        looking the name up via listing keystone users, as this requires admin
        rights in keystone, so FnGetAtt which calls _secret_accesskey won't
        work for normal non-admin users
        """
        # Lookup User resource by intrinsic reference (which is what is passed
        # into the UserName parameter.  Would be cleaner to just make the User
        # resource return resource_id for FnGetRefId but the AWS definition of
        # user does say it returns a user name not ID
        return self.stack.resource_by_refid(self.properties[self.USER_NAME])

    def handle_create(self):
        user = self._get_user()
        if user is None:
            raise exception.NotFound(_('could not find user %s') %
                                     self.properties[self.USER_NAME])

        kp = self.keystone().get_ec2_keypair(user.resource_id)
        if not kp:
            raise exception.Error(_("Error creating ec2 keypair for user %s") %
                                  user)

        self.resource_id_set(kp.access)
        self._secret = kp.secret

    def handle_delete(self):
        self._secret = None
        if self.resource_id is None:
            return

        user = self._get_user()
        if user is None:
            logger.warning(_('Error deleting %s - user not found') % str(self))
            return
        user_id = user.resource_id
        if user_id:
            try:
                self.keystone().delete_ec2_keypair(user_id, self.resource_id)
            except clients.hkc.kc.exceptions.NotFound:
                pass

        self.resource_id_set(None)

    def _secret_accesskey(self):
        '''
        Return the user's access key, fetching it from keystone if necessary
        '''
        if self._secret is None:
            if not self.resource_id:
                logger.warn(_('could not get secret for %(username)s '
                            'Error:%(msg)s') % {
                            'username': self.properties[self.USER_NAME],
                            'msg': "resource_id not yet set"})
            else:
                try:
                    user_id = self._get_user().resource_id
                    kp = self.keystone().get_ec2_keypair(user_id)
                except Exception as ex:
                    logger.warn(_('could not get secret for %(username)s '
                                'Error:%(msg)s') % {
                                'username': self.properties[self.USER_NAME],
                                'msg': str(ex)})
                else:
                    if kp.access == self.resource_id:
                        self._secret = kp.secret
                    else:
                        msg = (_("Unexpected ec2 keypair, for %(id)s access "
                               "%(access)s") % {
                               'id': user_id, 'access': kp.access})
                        logger.error(msg)

        return self._secret or '000-000-000'

    def FnGetAtt(self, key):
        res = None
        log_res = None
        if key == 'UserName':
            res = self.properties[self.USER_NAME]
            log_res = res
        elif key == 'SecretAccessKey':
            res = self._secret_accesskey()
            log_res = "<SANITIZED>"
        else:
            raise exception.InvalidTemplateAttribute(
                resource=self.physical_resource_name(), key=key)

        logger.info('%s.GetAtt(%s) == %s' % (self.physical_resource_name(),
                                             key, log_res))
        return unicode(res)

    def access_allowed(self, resource_name):
        return self._get_user().access_allowed(resource_name)


class AccessPolicy(resource.Resource):
    properties_schema = {
        'AllowedResources': {
            'Type': 'List',
            'Required': True,
            'Description': _('Resources that users are allowed to access by'
                             ' the DescribeStackResource API.')}}

    def handle_create(self):
        resources = self.properties['AllowedResources']
        # All of the provided resource names must exist in this stack
        for resource in resources:
            if resource not in self.stack:
                logger.error(_("AccessPolicy resource %s not in stack") %
                             resource)
                raise exception.ResourceNotFound(resource_name=resource,
                                                 stack_name=self.stack.name)

    def access_allowed(self, resource_name):
        return resource_name in self.properties['AllowedResources']


def resource_mapping():
    return {
        'AWS::IAM::User': User,
        'AWS::IAM::AccessKey': AccessKey,
        'OS::Heat::AccessPolicy': AccessPolicy,
    }
