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

import base64
from datetime import datetime

import six

from heat.common import exception
from heat.common import identifier
from heat.common import short_id
from heat.db import api as db_api
from heat.engine.attributes import Attributes
from heat.engine import event
from heat.engine import function
from heat.engine.properties import Properties
from heat.engine import resources
from heat.engine import rsrc_defn
from heat.engine import scheduler
from heat.engine import support
from heat.openstack.common import excutils
from heat.openstack.common.gettextutils import _
from heat.openstack.common import log as logging

logger = logging.getLogger(__name__)

DELETION_POLICY = (DELETE, RETAIN, SNAPSHOT) = ('Delete', 'Retain', 'Snapshot')


def get_types(support_status):
    '''Return a list of valid resource types.'''
    return resources.global_env().get_types(support_status)


def get_class(resource_type, resource_name=None):
    '''Return the Resource class for a given resource type.'''
    return resources.global_env().get_class(resource_type, resource_name)


def _register_class(resource_type, resource_class):
    resources.global_env().register_class(resource_type, resource_class)


class UpdateReplace(Exception):
    '''
    Raised when resource update requires replacement
    '''

    def __init__(self, resource_name='Unknown',
                 message=_("The Resource %s requires replacement.")):
        try:
            msg = message % resource_name
        except TypeError:
            msg = message
        super(Exception, self).__init__(msg)


class Metadata(object):
    '''
    A descriptor for accessing the metadata of a resource while ensuring the
    most up-to-date data is always obtained from the database.
    '''

    def __get__(self, resource, resource_class):
        '''Return the metadata for the owning resource.'''
        if resource is None:
            return None
        if resource.id is None:
            return resource.parsed_template('Metadata')
        rs = db_api.resource_get(resource.stack.context, resource.id)
        rs.refresh(attrs=['rsrc_metadata'])
        return rs.rsrc_metadata

    def __set__(self, resource, metadata):
        '''Update the metadata for the owning resource.'''
        if resource.id is None:
            raise exception.ResourceNotAvailable(resource_name=resource.name)
        rs = db_api.resource_get(resource.stack.context, resource.id)
        rs.update_and_save({'rsrc_metadata': metadata})


class Resource(object):
    ACTIONS = (INIT, CREATE, DELETE, UPDATE, ROLLBACK, SUSPEND, RESUME, ADOPT
               ) = ('INIT', 'CREATE', 'DELETE', 'UPDATE', 'ROLLBACK',
                    'SUSPEND', 'RESUME', 'ADOPT')

    STATUSES = (IN_PROGRESS, FAILED, COMPLETE
                ) = ('IN_PROGRESS', 'FAILED', 'COMPLETE')

    # If True, this resource must be created before it can be referenced.
    strict_dependency = True

    _metadata = Metadata()

    # Resource implementation set this to the subset of template keys
    # which are supported for handle_update, used by update_template_diff
    update_allowed_keys = ()

    # Resource implementation set this to the subset of resource properties
    # supported for handle_update, used by update_template_diff_properties
    update_allowed_properties = ()

    # Resource implementations set this to the name: description dictionary
    # that describes the appropriate resource attributes
    attributes_schema = {}

    # If True, this resource may perform authenticated API requests
    # throughout its lifecycle
    requires_deferred_auth = False

    # Limit to apply to physical_resource_name() size reduction algorithm.
    # If set to None no limit will be applied.
    physical_resource_name_limit = 255

    support_status = support.SupportStatus()

    def __new__(cls, name, definition, stack):
        '''Create a new Resource of the appropriate class for its type.'''

        assert isinstance(definition, rsrc_defn.ResourceDefinition)

        if cls != Resource:
            # Call is already for a subclass, so pass it through
            ResourceClass = cls
        else:
            # Select the correct subclass to instantiate
            ResourceClass = stack.env.get_class(definition.resource_type,
                                                resource_name=name)
            assert issubclass(ResourceClass, Resource)

        return super(Resource, cls).__new__(ResourceClass)

    def __init__(self, name, definition, stack):
        if '/' in name:
            raise ValueError(_('Resource name may not contain "/"'))

        self.stack = stack
        self.context = stack.context
        self.name = name
        self.t = definition
        self.reparse()
        self.attributes = Attributes(self.name,
                                     self.attributes_schema,
                                     self._resolve_attribute)

        self.abandon_in_progress = False

        if stack.id:
            resource = db_api.resource_get_by_name_and_stack(self.context,
                                                             name, stack.id)
        else:
            resource = None

        if resource:
            self.resource_id = resource.nova_instance
            self.action = resource.action
            self.status = resource.status
            self.status_reason = resource.status_reason
            self.id = resource.id
            try:
                self._data = db_api.resource_data_get_all(self, resource.data)
            except exception.NotFound:
                self._data = {}
            self.created_time = resource.created_at
            self.updated_time = resource.updated_at
        else:
            self.resource_id = None
            # if the stack is being deleted, assume we've already been deleted
            if stack.action == stack.DELETE:
                self.action = self.DELETE
            else:
                self.action = self.INIT
            self.status = self.COMPLETE
            self.status_reason = ''
            self.id = None
            self._data = {}
            self.created_time = None
            self.updated_time = None

    def reparse(self):
        self.properties = Properties(self.properties_schema,
                                     self.t.get('Properties', {}),
                                     function.resolve,
                                     self.name,
                                     self.context)

    def __eq__(self, other):
        '''Allow == comparison of two resources.'''
        # For the purposes of comparison, we declare two resource objects
        # equal if their names and parsed_templates are the same
        if isinstance(other, Resource):
            return (self.name == other.name) and (
                self.parsed_template() == other.parsed_template())
        return NotImplemented

    def __ne__(self, other):
        '''Allow != comparison of two resources.'''
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    @property
    def metadata(self):
        return self._metadata

    @metadata.setter
    def metadata(self, metadata):
        self._metadata = metadata

    def type(self):
        return self.t['Type']

    def has_interface(self, resource_type):
        """Check to see if this resource is either mapped to resource_type
        or is a "resource_type".
        """
        if self.type() == resource_type:
            return True
        ri = self.stack.env.get_resource_info(self.type(),
                                              self.name)
        return ri.name == resource_type

    def implementation_signature(self):
        '''
        Return a tuple defining the implementation.

        This should be broken down into a definition and an
        implementation version.
        '''

        return (self.__class__.__name__, self.support_status.version)

    def identifier(self):
        '''Return an identifier for this resource.'''
        return identifier.ResourceIdentifier(resource_name=self.name,
                                             **self.stack.identifier())

    def parsed_template(self, section=None, default={}):
        '''
        Return the parsed template data for the resource. May be limited to
        only one section of the data, in which case a default value may also
        be supplied.
        '''
        if section is None:
            template = self.t
        else:
            template = self.t.get(section, default)
        return function.resolve(template)

    def update_template_diff(self, after, before):
        '''
        Returns the difference between the before and after json snippets. If
        something has been removed in after which exists in before we set it to
        None. If any keys have changed which are not in update_allowed_keys,
        raises UpdateReplace if the differing keys are not in
        update_allowed_keys
        '''
        update_allowed_set = set(self.update_allowed_keys)

        # Create a set containing the keys in both current and update template
        template_keys = set(before.keys())
        template_keys.update(set(after.keys()))

        # Create a set of keys which differ (or are missing/added)
        changed_keys_set = set([k for k in template_keys
                                if before.get(k) != after.get(k)])

        if not changed_keys_set.issubset(update_allowed_set):
            raise UpdateReplace(self.name)

        return dict((k, after.get(k)) for k in changed_keys_set)

    def update_template_diff_properties(self, after_props, before_props):
        '''
        Returns the changed Properties between the before and after properties.
        If any properties have changed which are not in
        update_allowed_properties, raises UpdateReplace.
        '''
        update_allowed_set = set(self.update_allowed_properties)
        for (psk, psv) in self.properties.props.iteritems():
            if psv.update_allowed():
                update_allowed_set.add(psk)

        # Create a set of keys which differ (or are missing/added)
        changed_properties_set = set(k for k in after_props
                                     if before_props.get(k) !=
                                     after_props.get(k))

        if not changed_properties_set.issubset(update_allowed_set):
            raise UpdateReplace(self.name)

        return dict((k, after_props.get(k)) for k in changed_properties_set)

    def __str__(self):
        if self.stack.id:
            if self.resource_id:
                return '%s "%s" [%s] %s' % (self.__class__.__name__, self.name,
                                            self.resource_id, str(self.stack))
            return '%s "%s" %s' % (self.__class__.__name__, self.name,
                                   str(self.stack))
        return '%s "%s"' % (self.__class__.__name__, self.name)

    def add_dependencies(self, deps):
        for dep in self.t.dependencies(self.stack):
            deps += (self, dep)
        deps += (self, None)

    def required_by(self):
        '''
        Returns a list of names of resources which directly require this
        resource as a dependency.
        '''
        return list(
            [r.name for r in self.stack.dependencies.required_by(self)])

    def keystone(self):
        return self.stack.clients.keystone()

    def nova(self, service_type='compute'):
        return self.stack.clients.nova(service_type)

    def swift(self):
        return self.stack.clients.swift()

    def neutron(self):
        return self.stack.clients.neutron()

    def cinder(self):
        return self.stack.clients.cinder()

    def trove(self):
        return self.stack.clients.trove()

    def ceilometer(self):
        return self.stack.clients.ceilometer()

    def heat(self):
        return self.stack.clients.heat()

    def _do_action(self, action, pre_func=None, resource_data=None):
        '''
        Perform a transition to a new state via a specified action
        action should be e.g self.CREATE, self.UPDATE etc, we set
        status based on this, the transition is handled by calling the
        corresponding handle_* and check_*_complete functions
        Note pre_func is an optional function reference which will
        be called before the handle_<action> function

        If the resource does not declare a check_$action_complete function,
        we declare COMPLETE status as soon as the handle_$action call has
        finished, and if no handle_$action function is declared, then we do
        nothing, useful e.g if the resource requires no action for a given
        state transition
        '''
        assert action in self.ACTIONS, 'Invalid action %s' % action

        try:
            self.state_set(action, self.IN_PROGRESS)

            action_l = action.lower()
            handle = getattr(self, 'handle_%s' % action_l, None)
            check = getattr(self, 'check_%s_complete' % action_l, None)

            if callable(pre_func):
                pre_func()

            handle_data = None
            if callable(handle):
                handle_data = (handle(resource_data) if resource_data else
                               handle())
                yield
                if callable(check):
                    while not check(handle_data):
                        yield
        except Exception as ex:
            logger.exception('%s : %s' % (action, str(self)))
            failure = exception.ResourceFailure(ex, self, action)
            self.state_set(action, self.FAILED, six.text_type(failure))
            raise failure
        except:
            with excutils.save_and_reraise_exception():
                try:
                    self.state_set(action, self.FAILED,
                                   '%s aborted' % action)
                except Exception:
                    logger.exception(_('Error marking resource as failed'))
        else:
            self.state_set(action, self.COMPLETE)

    def preview(self):
        '''
        Default implementation of Resource.preview.

        This method should be overridden by child classes for specific
        behavior.
        '''
        return self

    def create(self):
        '''
        Create the resource. Subclasses should provide a handle_create() method
        to customise creation.
        '''
        action = self.CREATE
        if (self.action, self.status) != (self.INIT, self.COMPLETE):
            exc = exception.Error(_('State %s invalid for create')
                                  % str(self.state))
            raise exception.ResourceFailure(exc, self, action)

        logger.info('creating %s' % str(self))

        # Re-resolve the template, since if the resource Ref's
        # the StackId pseudo parameter, it will change after
        # the parser.Stack is stored (which is after the resources
        # are __init__'d, but before they are create()'d)
        self.reparse()
        return self._do_action(action, self.properties.validate)

    def prepare_abandon(self):
        self.abandon_in_progress = True
        return {
            'name': self.name,
            'resource_id': self.resource_id,
            'type': self.type(),
            'action': self.action,
            'status': self.status,
            'metadata': self.metadata,
            'resource_data': self.data()
        }

    def adopt(self, resource_data):
        '''
        Adopt the existing resource. Resource subclasses can provide
        a handle_adopt() method to customise adopt.
        '''
        return self._do_action(self.ADOPT, resource_data=resource_data)

    def handle_adopt(self, resource_data=None):
        resource_id, data, metadata = self._get_resource_info(resource_data)

        if not resource_id:
            exc = Exception(_('Resource ID was not provided.'))
            failure = exception.ResourceFailure(exc, self)
            raise failure

        # set resource id
        self.resource_id_set(resource_id)

        # save the resource data
        if data and isinstance(data, dict):
            for key, value in data.iteritems():
                self.data_set(key, value)

        # save the resource metadata
        self.metadata = metadata

    def _get_resource_info(self, resource_data):
        if not resource_data:
            return None, None, None

        return (resource_data.get('resource_id'),
                resource_data.get('resource_data'),
                resource_data.get('metadata'))

    def update(self, after, before=None, prev_resource=None):
        '''
        update the resource. Subclasses should provide a handle_update() method
        to customise update, the base-class handle_update will fail by default.
        '''
        action = self.UPDATE

        (cur_class_def, cur_ver) = self.implementation_signature()
        prev_ver = cur_ver
        if prev_resource is not None:
            (prev_class_def,
             prev_ver) = prev_resource.implementation_signature()
            if prev_class_def != cur_class_def:
                raise UpdateReplace(self.name)

        if before is None:
            before = self.parsed_template()
        if prev_ver == cur_ver and before == after:
            return

        if (self.action, self.status) in ((self.CREATE, self.IN_PROGRESS),
                                          (self.UPDATE, self.IN_PROGRESS),
                                          (self.ADOPT, self.IN_PROGRESS)):
            exc = Exception(_('Resource update already requested'))
            raise exception.ResourceFailure(exc, self, action)

        logger.info('updating %s' % str(self))

        try:
            self.updated_time = datetime.utcnow()
            self.state_set(action, self.IN_PROGRESS)
            before_properties = Properties(self.properties_schema,
                                           before.get('Properties', {}),
                                           function.resolve,
                                           self.name,
                                           self.context)
            after_properties = Properties(self.properties_schema,
                                          after.get('Properties', {}),
                                          function.resolve,
                                          self.name,
                                          self.context)
            after_properties.validate()
            tmpl_diff = self.update_template_diff(function.resolve(after),
                                                  before)
            prop_diff = self.update_template_diff_properties(after_properties,
                                                             before_properties)
            if callable(getattr(self, 'handle_update', None)):
                handle_data = self.handle_update(after, tmpl_diff, prop_diff)
                yield
                if callable(getattr(self, 'check_update_complete', None)):
                    while not self.check_update_complete(handle_data):
                        yield
        except UpdateReplace:
            with excutils.save_and_reraise_exception():
                logger.debug(_("Resource %s update requires replacement") %
                             self.name)
        except Exception as ex:
            logger.exception(_('update %(resource)s : %(err)s') %
                             {'resource': str(self), 'err': ex})
            failure = exception.ResourceFailure(ex, self, action)
            self.state_set(action, self.FAILED, six.text_type(failure))
            raise failure
        else:
            self.t = after
            self.reparse()
            self.state_set(action, self.COMPLETE)

    def suspend(self):
        '''
        Suspend the resource.  Subclasses should provide a handle_suspend()
        method to implement suspend
        '''
        action = self.SUSPEND

        # Don't try to suspend the resource unless it's in a stable state
        if (self.action == self.DELETE or self.status != self.COMPLETE):
            exc = exception.Error(_('State %s invalid for suspend')
                                  % str(self.state))
            raise exception.ResourceFailure(exc, self, action)

        logger.info(_('suspending %s') % str(self))
        return self._do_action(action)

    def resume(self):
        '''
        Resume the resource.  Subclasses should provide a handle_resume()
        method to implement resume
        '''
        action = self.RESUME

        # Can't resume a resource unless it's SUSPEND_COMPLETE
        if self.state != (self.SUSPEND, self.COMPLETE):
            exc = exception.Error(_('State %s invalid for resume')
                                  % str(self.state))
            raise exception.ResourceFailure(exc, self, action)

        logger.info(_('resuming %s') % str(self))
        return self._do_action(action)

    def physical_resource_name(self):
        if self.id is None:
            return None

        name = '%s-%s-%s' % (self.stack.name,
                             self.name,
                             short_id.get_id(self.id))

        if self.physical_resource_name_limit:
            name = self.reduce_physical_resource_name(
                name, self.physical_resource_name_limit)
        return name

    @staticmethod
    def reduce_physical_resource_name(name, limit):
        '''
        Reduce length of physical resource name to a limit.

        The reduced name will consist of the following:

        * the first 2 characters of the name
        * a hyphen
        * the end of the name, truncated on the left to bring
          the name length within the limit

        :param name: The name to reduce the length of
        :param limit: The max length limit
        :returns: A name whose length is less than or equal to the limit
        '''
        if len(name) <= limit:
            return name

        if limit < 4:
            raise ValueError(_('limit cannot be less than 4'))

        postfix_length = limit - 3
        return name[0:2] + '-' + name[-postfix_length:]

    def validate(self):
        logger.info(_('Validating %s') % str(self))

        function.validate(self.t)
        self.validate_deletion_policy(self.t)
        return self.properties.validate()

    @classmethod
    def validate_deletion_policy(cls, template):
        deletion_policy = template.get('DeletionPolicy', DELETE)
        if deletion_policy not in DELETION_POLICY:
            msg = _('Invalid DeletionPolicy %s') % deletion_policy
            raise exception.StackValidationFailed(message=msg)
        elif deletion_policy == SNAPSHOT:
            if not callable(getattr(cls, 'handle_snapshot_delete', None)):
                msg = _('Snapshot DeletionPolicy not supported')
                raise exception.StackValidationFailed(message=msg)

    def delete(self):
        '''
        Delete the resource. Subclasses should provide a handle_delete() method
        to customise deletion.
        '''
        action = self.DELETE

        if (self.action, self.status) == (self.DELETE, self.COMPLETE):
            return
        # No need to delete if the resource has never been created
        if self.action == self.INIT:
            return

        initial_state = self.state

        logger.info(_('deleting %s') % str(self))

        try:
            self.state_set(action, self.IN_PROGRESS)

            if self.abandon_in_progress:
                deletion_policy = RETAIN
            else:
                deletion_policy = self.t.get('DeletionPolicy', DELETE)
            handle_data = None
            if deletion_policy == DELETE:
                if callable(getattr(self, 'handle_delete', None)):
                    handle_data = self.handle_delete()
                    yield
            elif deletion_policy == SNAPSHOT:
                if callable(getattr(self, 'handle_snapshot_delete', None)):
                    handle_data = self.handle_snapshot_delete(initial_state)
                    yield

            if (deletion_policy != RETAIN and
                    callable(getattr(self, 'check_delete_complete', None))):
                while not self.check_delete_complete(handle_data):
                    yield

        except Exception as ex:
            logger.exception(_('Delete %s') % str(self))
            failure = exception.ResourceFailure(ex, self, self.action)
            self.state_set(action, self.FAILED, six.text_type(failure))
            raise failure
        except:
            with excutils.save_and_reraise_exception():
                try:
                    self.state_set(action, self.FAILED,
                                   'Deletion aborted')
                except Exception:
                    logger.exception(_('Error marking resource deletion '
                                     'failed'))
        else:
            self.state_set(action, self.COMPLETE)

    @scheduler.wrappertask
    def destroy(self):
        '''
        Delete the resource and remove it from the database.
        '''
        yield self.delete()

        if self.id is None:
            return

        try:
            db_api.resource_get(self.context, self.id).delete()
        except exception.NotFound:
            # Don't fail on delete if the db entry has
            # not been created yet.
            pass

        self.id = None

    def resource_id_set(self, inst):
        self.resource_id = inst
        if self.id is not None:
            try:
                rs = db_api.resource_get(self.context, self.id)
                rs.update_and_save({'nova_instance': self.resource_id})
            except Exception as ex:
                logger.warn(_('db error %s') % ex)

    def _store(self):
        '''Create the resource in the database.'''
        metadata = self.metadata
        try:
            rs = {'action': self.action,
                  'status': self.status,
                  'status_reason': self.status_reason,
                  'stack_id': self.stack.id,
                  'nova_instance': self.resource_id,
                  'name': self.name,
                  'rsrc_metadata': metadata,
                  'stack_name': self.stack.name}

            new_rs = db_api.resource_create(self.context, rs)
            self.id = new_rs.id
            self.created_time = new_rs.created_at
        except Exception as ex:
            logger.error(_('DB error %s') % ex)

    def _add_event(self, action, status, reason):
        '''Add a state change event to the database.'''
        ev = event.Event(self.context, self.stack, action, status, reason,
                         self.resource_id, self.properties,
                         self.name, self.type())

        ev.store()

    def _store_or_update(self, action, status, reason):
        self.action = action
        self.status = status
        self.status_reason = reason

        if self.id is not None:
            try:
                rs = db_api.resource_get(self.context, self.id)
                rs.update_and_save({'action': self.action,
                                    'status': self.status,
                                    'status_reason': reason,
                                    'stack_id': self.stack.id,
                                    'updated_at': self.updated_time,
                                    'nova_instance': self.resource_id})
            except Exception as ex:
                logger.error(_('DB error %s') % ex)

        # store resource in DB on transition to CREATE_IN_PROGRESS
        # all other transitions (other than to DELETE_COMPLETE)
        # should be handled by the update_and_save above..
        elif (action, status) in [(self.CREATE, self.IN_PROGRESS),
                                  (self.ADOPT, self.IN_PROGRESS)]:
            self._store()

    def _resolve_attribute(self, name):
        """
        Default implementation; should be overridden by resources that expose
        attributes

        :param name: The attribute to resolve
        :returns: the resource attribute named key
        """
        # By default, no attributes resolve
        pass

    def state_reset(self):
        """
        Reset state to (INIT, COMPLETE)
        """
        self.action = self.INIT
        self.status = self.COMPLETE

    def state_set(self, action, status, reason="state changed"):
        if action not in self.ACTIONS:
            raise ValueError(_("Invalid action %s") % action)

        if status not in self.STATUSES:
            raise ValueError(_("Invalid status %s") % status)

        old_state = (self.action, self.status)
        new_state = (action, status)
        self._store_or_update(action, status, reason)

        if new_state != old_state:
            self._add_event(action, status, reason)

    @property
    def state(self):
        '''Returns state, tuple of action, status.'''
        return (self.action, self.status)

    def FnGetRefId(self):
        '''
        For the intrinsic function Ref.

        :results: the id or name of the resource.
        '''
        if self.resource_id is not None:
            return unicode(self.resource_id)
        else:
            return unicode(self.name)

    def FnGetAtt(self, key):
        '''
        For the intrinsic function Fn::GetAtt.

        :param key: the attribute key.
        :returns: the attribute value.
        '''
        try:
            return self.attributes[key]
        except KeyError:
            raise exception.InvalidTemplateAttribute(resource=self.name,
                                                     key=key)

    def FnBase64(self, data):
        '''
        For the instrinsic function Fn::Base64.

        :param data: the input data.
        :returns: the Base64 representation of the input data.
        '''
        return base64.b64encode(data)

    def signal(self, details=None):
        '''
        signal the resource. Subclasses should provide a handle_signal() method
        to implement the signal, the base-class raise an exception if no
        handler is implemented.
        '''
        def get_string_details():
            if details is None:
                return 'No signal details provided'
            if isinstance(details, basestring):
                return details
            if isinstance(details, dict):
                if all(k in details for k in ('previous', 'current',
                                              'reason')):
                    # this is from Ceilometer.
                    auto = '%(previous)s to %(current)s (%(reason)s)' % details
                    return 'alarm state changed from %s' % auto
                elif 'state' in details:
                    # this is from watchrule
                    return 'alarm state changed to %(state)s' % details

            return 'Unknown'

        try:
            if not callable(getattr(self, 'handle_signal', None)):
                msg = (_('Resource %s is not able to receive a signal') %
                       str(self))
                raise Exception(msg)

            self._add_event('signal', self.status, get_string_details())
            self.handle_signal(details)
        except Exception as ex:
            logger.exception(_('signal %(name)s : %(msg)s') %
                             {'name': str(self), 'msg': ex})
            failure = exception.ResourceFailure(ex, self)
            raise failure

    def handle_update(self, json_snippet=None, tmpl_diff=None, prop_diff=None):
        raise UpdateReplace(self.name)

    def metadata_update(self, new_metadata=None):
        '''
        No-op for resources which don't explicitly override this method
        '''
        if new_metadata:
            logger.warning(_("Resource %s does not implement metadata update")
                           % self.name)

    @classmethod
    def resource_to_template(cls, resource_type):
        '''
        :param resource_type: The resource type to be displayed in the template
        :returns: A template where the resource's properties_schema is mapped
            as parameters, and the resource's attributes_schema is mapped as
            outputs
        '''
        (parameters, properties) = (Properties.
                                    schema_to_parameters_and_properties(
                                        cls.properties_schema))

        resource_name = cls.__name__
        return {
            'HeatTemplateFormatVersion': '2012-12-12',
            'Parameters': parameters,
            'Resources': {
                resource_name: {
                    'Type': resource_type,
                    'Properties': properties
                }
            },
            'Outputs': Attributes.as_outputs(resource_name, cls)
        }

    def data(self):
        '''
        Resource data for this resource

        Use methods data_set and data_delete to modify the resource data
        for this resource.

        :returns: a dict representing the resource data for this resource.
        '''
        if self._data is None and self.id:
            try:
                self._data = db_api.resource_data_get_all(self)
            except exception.NotFound:
                pass

        return self._data or {}

    def data_set(self, key, value, redact=False):
        '''Save resource's key/value pair to database.'''
        db_api.resource_data_set(self, key, value, redact)
        # force fetch all resource data from the database again
        self._data = None

    def data_delete(self, key):
        '''
        Remove a resource_data element associated to a resource.

        :returns: True if the key existed to delete
        '''
        try:
            db_api.resource_data_delete(self, key)
        except exception.NotFound:
            return False
        else:
            # force fetch all resource data from the database again
            self._data = None
            return True
