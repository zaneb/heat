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

import functools
import itertools
import types

from time import sleep
from time import time as wallclock

from heat.openstack.common import excutils
from heat.openstack.common import log as logging

logger = logging.getLogger(__name__)


def task_description(task):
    """
    Return a human-readable string description of a task suitable for logging
    the status of the task.
    """
    if isinstance(task, types.MethodType):
        name = getattr(task, '__name__')
        obj = getattr(task, '__self__')
        if name is not None and obj is not None:
            return '%s from %s' % (name, obj)
    return repr(task)


class Timeout(BaseException):
    """
    Timeout exception, raised within a task when it has exceeded its allotted
    (wallclock) running time.

    This allows the task to perform any necessary cleanup, as well as use a
    different exception to notify the controlling task if appropriate. If the
    task supresses the exception altogether, it will be cancelled but the
    controlling task will not be notified of the timeout.
    """

    def __init__(self, task_runner, timeout):
        """
        Initialise with the TaskRunner and a timeout period in seconds.
        """
        message = _('%s Timed out') % task_runner
        super(Timeout, self).__init__(message)

        # Note that we don't attempt to handle leap seconds or large clock
        # jumps here. The latter are assumed to be rare and the former
        # negligible in the context of the timeout. Time zone adjustments,
        # Daylight Savings and the like *are* handled. PEP 418 adds a proper
        # monotonic clock, but only in Python 3.3.
        self._endtime = wallclock() + timeout

    def expired(self):
        return wallclock() > self._endtime


class TaskRunner(object):
    """
    Wrapper for a resumable task (co-routine).
    """

    def __init__(self, task, *args, **kwargs):
        """
        Initialise with a task function, and arguments to be passed to it when
        it is started.

        The task function may be a co-routine that yields control flow between
        steps.
        """
        assert callable(task), "Task is not callable"

        self._task = task
        self._args = args
        self._kwargs = kwargs
        self._runner = None
        self._done = False
        self._timeout = None
        self.name = task_description(task)

    def __str__(self):
        """Return a human-readable string representation of the task."""
        return 'Task %s' % self.name

    def _sleep(self, wait_time):
        """Sleep for the specified number of seconds."""
        if wait_time is not None:
            logger.debug('%s sleeping' % str(self))
            sleep(wait_time)

    def __call__(self, wait_time=1, timeout=None):
        """
        Start and run the task to completion.

        The task will sleep for `wait_time` seconds between steps. To avoid
        sleeping, pass `None` for `wait_time`.
        """
        self.start(timeout=timeout)
        while not self.step():
            self._sleep(wait_time)

    def start(self, timeout=None):
        """
        Initialise the task and run its first step.

        If a timeout is specified, any attempt to step the task after that
        number of seconds has elapsed will result in a Timeout being
        raised inside the task.
        """
        assert self._runner is None, "Task already started"

        logger.debug('%s starting' % str(self))

        if timeout is not None:
            self._timeout = Timeout(self, timeout)

        result = self._task(*self._args, **self._kwargs)
        if isinstance(result, types.GeneratorType):
            self._runner = result
            self.step()
        else:
            self._runner = False
            self._done = True
            logger.debug('%s done (not resumable)' % str(self))

    def step(self):
        """
        Run another step of the task, and return True if the task is complete;
        False otherwise.
        """
        if self:
            assert self._runner is not None, "Task not started"

            if self._timeout is not None and self._timeout.expired():
                logger.info('%s timed out' % str(self))

                try:
                    self._runner.throw(self._timeout)
                except StopIteration:
                    self._done = True
                else:
                    # Clean up in case task swallows exception without exiting
                    self.cancel()
            else:
                logger.debug('%s running' % str(self))

                try:
                    next(self._runner)
                except StopIteration:
                    self._done = True
                    logger.debug('%s complete' % str(self))

        return self._done

    def cancel(self):
        """Cancel the task if it is running."""
        if self.started() and not self.done():
            logger.debug('%s cancelled' % str(self))
            self._runner.close()
            self._done = True

    def started(self):
        """Return True if the task has been started."""
        return self._runner is not None

    def done(self):
        """Return True if the task is complete."""
        return self._done

    def __nonzero__(self):
        """Return True if there are steps remaining."""
        return not self.done()


class TaskGroup(object):
    """
    A task which manages a group of subtasks.

    When the task is started, all of its subtasks are also started. The task
    completes when all subtasks are complete.

    Once started, the subtasks are assumed to be only polling for completion
    of an asynchronous operation, so no attempt is made to give them equal
    scheduling slots.
    """

    def __init__(self, tasks, name=None):
        """Initialise with a list of tasks"""
        self._tasks = list(tasks)
        if name is None:
            name = ', '.join(task_description(t) for t in self._tasks)
        self.name = name

    @staticmethod
    def _args(arg_lists):
        """Return a list containing the positional args for each subtask."""
        return zip(*arg_lists)

    @staticmethod
    def _kwargs(kwarg_lists):
        """Return a list containing the keyword args for each subtask."""
        keygroups = (itertools.izip(itertools.repeat(name),
                                    arglist)
                     for name, arglist in kwarg_lists.iteritems())
        return [dict(kwargs) for kwargs in itertools.izip(*keygroups)]

    @classmethod
    def from_task_with_args(cls, task, *arg_lists, **kwarg_lists):
        """
        Return a new TaskGroup where each subtask is identical except for the
        arguments passed to it.

        Each argument to use should be passed as a list (or iterable) of values
        such that one is passed in the corresponding position for each subtask.
        The number of subtasks spawned depends on the length of the argument
        lists. If multiple arguments are supplied, each should be of the same
        length. In the case of any discrepancy, the length of the shortest
        argument list will be used, and any extra arguments discarded.
        """

        args_list = cls._args(arg_lists)
        kwargs_list = cls._kwargs(kwarg_lists)

        if kwarg_lists and not arg_lists:
            args_list = [[]] * len(kwargs_list)
        elif arg_lists and not kwarg_lists:
            kwargs_list = [{}] * len(args_list)

        task_args = itertools.izip(args_list, kwargs_list)
        tasks = (functools.partial(task, *a, **kwa) for a, kwa in task_args)

        return cls(tasks, name=task_description(task))

    def __repr__(self):
        """Return a string representation of the task group."""
        return '%s(%s)' % (type(self).__name__, self.name)

    def __call__(self):
        """Return a co-routine which runs the task group"""
        runners = [TaskRunner(t) for t in self._tasks]

        try:
            for r in runners:
                r.start()

            while runners:
                yield
                runners = list(itertools.dropwhile(lambda r: r.step(),
                                                   runners))
        except:
            with excutils.save_and_reraise_exception():
                for r in self.runners:
                    r.cancel()
