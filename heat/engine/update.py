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

from heat.engine import dependencies
from heat.engine import resource
from heat.engine import scheduler

from heat.openstack.common import log as logging

logger = logging.getLogger(__name__)


class StackUpdate(object):
    """
    A Task to perform the update of an existing stack to a new template.
    """

    def __init__(self, existing_stack, new_stack, previous_stack):
        """Initialise with the existing stack and the new stack."""
        self.existing_stack = existing_stack
        self.new_stack = new_stack
        self.previous_stack = previous_stack

        self.existing_snippets = dict((r.name, r.parsed_template())
                                      for r in self.existing_stack)

    def __str__(self):
        return '%s Update' % str(self.existing_stack)

    @scheduler.wrappertask
    def __call__(self):
        """Return a co-routine that updates the stack."""

        update_task = scheduler.DependencyTaskGroup(self.dependencies(),
                                                    self._resource_update)
        try:
            yield update_task()
        finally:
            prev_deps = self.previous_stack._get_dependencies(
                self.previous_stack.resources.itervalues())
            self.previous_stack.dependencies = prev_deps

    def _resource_update(self, res):
        if res.name in self.new_stack and self.new_stack[res.name] is res:
            return self._process_new_resource_update(res)
        else:
            return self._process_existing_resource_update(res)

    @scheduler.wrappertask
    def _process_new_resource_update(self, new_res):
        res_name = new_res.name

        if res_name in self.existing_stack:
            existing_res = self.existing_stack[res_name]
            try:
                yield self._update_in_place(existing_res,
                                            new_res)
            except resource.UpdateReplace:
                # Retain for possible later rollback
                existing_res.stack = self.previous_stack
                self.previous_stack[res_name] = existing_res
            else:
                logger.info("Resource %s for stack %s updated" %
                            (res_name, self.existing_stack.name))
                return

        new_res.stack = self.existing_stack
        self.existing_stack[res_name] = new_res

        if new_res.state is None:
            yield new_res.create()
        else:
            new_res.state_set(new_res.UPDATE_COMPLETE)

    @scheduler.wrappertask
    def _update_in_place(self, existing_res, new_res):
        # Compare resolved pre/post update resource snippets,
        # note the new resource snippet is resolved in the context
        # of the existing stack (which is the stack being updated)
        existing_snippet = self.existing_snippets[existing_res.name]
        new_snippet = self.existing_stack.resolve_runtime_data(new_res.t)

        if new_snippet != existing_snippet:
            yield existing_res.update(new_snippet, existing_snippet)

    @scheduler.wrappertask
    def _process_existing_resource_update(self, existing_res):
        res_name = existing_res.name

        if res_name in self.new_stack:
            if self.new_stack[res_name].state is None:
                # Already updated in-place
                return

        # Remove from rollback cache
        if res_name in self.previous_stack:
            previous = resource.Resource(res_name,
                                         self.previous_stack[res_name].t,
                                         self.previous_stack)
            self.previous_stack[res_name] = previous

        yield existing_res.destroy()

        if res_name not in self.new_stack:
            del self.existing_stack.resources[res_name]

    def dependencies(self):
        '''
        Return a Dependencies object representing the dependencies between
        update operations to move from an existing stack definition to a new
        one.
        '''
        existing_deps = self.existing_stack.dependencies
        new_deps = self.new_stack.dependencies

        def edges():
            # Create/update the new stack's resources in create order
            for e in new_deps.graph().edges():
                yield e
            # Destroy/cleanup the old stack's resources in delete order
            for e in existing_deps.graph(reverse=True).edges():
                yield e
            # Don't cleanup old resources until after they have been replaced
            for res in self.existing_stack:
                if res.name in self.new_stack:
                    yield (res, self.new_stack[res.name])

        return dependencies.Dependencies(edges())
