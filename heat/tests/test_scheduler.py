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
from nose.plugins.attrib import attr

import contextlib
import eventlet

from heat.engine import scheduler


class DummyTask(object):
    def __init__(self, num_steps=3):
        self.num_steps = num_steps

    def __call__(self, *args, **kwargs):
        for i in range(1, self.num_steps + 1):
            self.do_step(i, *args, **kwargs)
            yield

    def do_step(self, step_num, *args, **kwargs):
        print self, step_num


@attr(tag=['unit', 'scheduler'])
@attr(speed='fast')
class TaskGroupTest(mox.MoxTestBase):

    def test_group(self):
        tasks = [DummyTask() for i in range(3)]
        for t in tasks:
            self.mox.StubOutWithMock(t, 'do_step')

        self.mox.StubOutWithMock(scheduler.TaskRunner, '_sleep')

        for t in tasks:
            t.do_step(1).AndReturn(None)
        for t in tasks:
            scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
            t.do_step(2).AndReturn(None)
            scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
            t.do_step(3).AndReturn(None)

        self.mox.ReplayAll()

        tg = scheduler.TaskGroup(tasks)
        scheduler.TaskRunner(tg)()

    def test_kwargs(self):
        input_kwargs = {'i': [0, 1, 2],
                        'i2': [0, 1, 4]}

        output_kwargs = scheduler.TaskGroup._kwargs(input_kwargs)

        expected_kwargs = [{'i': 0, 'i2': 0},
                           {'i': 1, 'i2': 1},
                           {'i': 2, 'i2': 4}]

        self.assertEqual(list(output_kwargs), expected_kwargs)

    def test_kwargs_short(self):
        input_kwargs = {'i': [0, 1, 2],
                        'i2': [0]}

        output_kwargs = scheduler.TaskGroup._kwargs(input_kwargs)

        expected_kwargs = [{'i': 0, 'i2': 0}]

        self.assertEqual(list(output_kwargs), expected_kwargs)

    def test_no_kwargs(self):
        output_kwargs = scheduler.TaskGroup._kwargs({})
        self.assertEqual(list(output_kwargs), [])

    def test_args(self):
        input_args = ([0, 1, 2],
                      [0, 1, 4])

        output_args = scheduler.TaskGroup._args(input_args)

        expected_args = [(0, 0), (1, 1), (2, 4)]

        self.assertEqual(list(output_args), expected_args)

    def test_args_short(self):
        input_args = ([0, 1, 2],
                      [0])

        output_args = scheduler.TaskGroup._args(input_args)

        expected_args = [(0, 0)]

        self.assertEqual(list(output_args), expected_args)

    def test_no_args(self):
        output_args = scheduler.TaskGroup._args([])
        self.assertEqual(list(output_args), [])

    @contextlib.contextmanager
    def _args_test(self, *arg_lists, **kwarg_lists):
        dummy = DummyTask(1)

        tg = scheduler.TaskGroup.from_task_with_args(dummy,
                                                     *arg_lists,
                                                     **kwarg_lists)

        self.mox.StubOutWithMock(dummy, 'do_step')
        yield dummy

        self.mox.ReplayAll()
        scheduler.TaskRunner(tg)(wait_time=None)
        self.mox.VerifyAll()

    def test_with_all_args(self):
        with self._args_test([0, 1, 2], [0, 1, 8],
                             i=[0, 1, 2], i2=[0, 1, 4]) as dummy:
            for i in range(3):
                dummy.do_step(1, i, i * i * i, i=i, i2=i * i)

    def test_with_short_args(self):
        with self._args_test([0, 1, 2], [0, 1],
                             i=[0, 1, 2], i2=[0, 1, 4]) as dummy:
            for i in range(2):
                dummy.do_step(1, i, i * i, i=i, i2=i * i)

    def test_with_short_kwargs(self):
        with self._args_test([0, 1, 2], [0, 1, 8],
                             i=[0, 1], i2=[0, 1, 4]) as dummy:
            for i in range(2):
                dummy.do_step(1, i, i * i, i=i, i2=i * i)

    def test_with_empty_args(self):
        with self._args_test([],
                             i=[0, 1, 2], i2=[0, 1, 4]) as dummy:
            pass

    def test_with_empty_kwargs(self):
        with self._args_test([0, 1, 2], [0, 1, 8],
                             i=[]) as dummy:
            pass

    def test_with_no_args(self):
        with self._args_test(i=[0, 1, 2], i2=[0, 1, 4]) as dummy:
            for i in range(3):
                dummy.do_step(1, i=i, i2=i * i)

    def test_with_no_kwargs(self):
        with self._args_test([0, 1, 2], [0, 1, 4]) as dummy:
            for i in range(3):
                dummy.do_step(1, i, i * i)


@attr(tag=['unit', 'scheduler'])
@attr(speed='fast')
class TaskTest(mox.MoxTestBase):

    def test_run(self):
        task = DummyTask()
        self.mox.StubOutWithMock(task, 'do_step')
        self.mox.StubOutWithMock(scheduler.TaskRunner, '_sleep')

        task.do_step(1).AndReturn(None)
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
        task.do_step(2).AndReturn(None)
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
        task.do_step(3).AndReturn(None)

        self.mox.ReplayAll()

        scheduler.TaskRunner(task)()

    def test_sleep(self):
        sleep_time = 42
        self.mox.StubOutWithMock(eventlet, 'sleep')
        eventlet.sleep(sleep_time).MultipleTimes().AndReturn(None)

        self.mox.ReplayAll()

        runner = scheduler.TaskRunner(DummyTask())
        runner(wait_time=sleep_time)

    def test_sleep_zero(self):
        self.mox.StubOutWithMock(eventlet, 'sleep')
        eventlet.sleep(0).MultipleTimes().AndReturn(None)

        self.mox.ReplayAll()

        runner = scheduler.TaskRunner(DummyTask())
        runner(wait_time=0)

    def test_sleep_none(self):
        self.mox.StubOutWithMock(eventlet, 'sleep')
        self.mox.ReplayAll()

        runner = scheduler.TaskRunner(DummyTask())
        runner(wait_time=None)

    def test_args(self):
        args = ['foo', 'bar']
        kwargs = {'baz': 'quux', 'blarg': 'wibble'}

        self.mox.StubOutWithMock(DummyTask, '__call__')
        task = DummyTask()

        task(*args, **kwargs)

        self.mox.ReplayAll()

        runner = scheduler.TaskRunner(task, *args, **kwargs)
        runner(wait_time=None)

    def test_non_callable(self):
        self.assertRaises(AssertionError, scheduler.TaskRunner, object())

    def test_stepping(self):
        task = DummyTask()
        self.mox.StubOutWithMock(task, 'do_step')
        self.mox.StubOutWithMock(scheduler.TaskRunner, '_sleep')

        task.do_step(1).AndReturn(None)
        task.do_step(2).AndReturn(None)
        task.do_step(3).AndReturn(None)

        self.mox.ReplayAll()

        runner = scheduler.TaskRunner(task)
        runner.start()

        self.assertFalse(runner.step())
        self.assertTrue(runner)
        self.assertFalse(runner.step())
        self.assertTrue(runner.step())
        self.assertFalse(runner)

    def test_start_no_steps(self):
        task = DummyTask(0)
        self.mox.StubOutWithMock(task, 'do_step')
        self.mox.StubOutWithMock(scheduler.TaskRunner, '_sleep')

        self.mox.ReplayAll()

        runner = scheduler.TaskRunner(task)
        runner.start()

        self.assertTrue(runner.done())
        self.assertTrue(runner.step())

    def test_start_only(self):
        task = DummyTask()
        self.mox.StubOutWithMock(task, 'do_step')
        self.mox.StubOutWithMock(scheduler.TaskRunner, '_sleep')

        task.do_step(1).AndReturn(None)

        self.mox.ReplayAll()

        runner = scheduler.TaskRunner(task)

        self.assertFalse(runner.started())
        runner.start()
        self.assertTrue(runner.started())

    def test_double_start(self):
        runner = scheduler.TaskRunner(DummyTask())

        runner.start()
        self.assertRaises(AssertionError, runner.start)

    def test_call_double_start(self):
        runner = scheduler.TaskRunner(DummyTask())

        runner(wait_time=None)
        self.assertRaises(AssertionError, runner.start)

    def test_start_function(self):
        def task():
            pass

        runner = scheduler.TaskRunner(task)

        runner.start()
        self.assertTrue(runner.started())
        self.assertTrue(runner.done())
        self.assertTrue(runner.step())

    def test_repeated_done(self):
        task = DummyTask(0)
        self.mox.StubOutWithMock(task, 'do_step')
        self.mox.StubOutWithMock(scheduler.TaskRunner, '_sleep')

        self.mox.ReplayAll()

        runner = scheduler.TaskRunner(task)

        runner.start()
        self.assertTrue(runner.step())
        self.assertTrue(runner.step())

    def test_timeout(self):
        st = scheduler.wallclock()

        def task():
            while True:
                yield

        self.mox.StubOutWithMock(scheduler, 'wallclock')
        scheduler.wallclock().AndReturn(st)
        scheduler.wallclock().AndReturn(st + 0.5)
        scheduler.wallclock().AndReturn(st + 1.5)

        self.mox.ReplayAll()

        runner = scheduler.TaskRunner(task)

        runner.start(timeout=1)
        self.assertTrue(runner)
        self.assertRaises(scheduler.Timeout, runner.step)

        self.mox.VerifyAll()

    def test_timeout_return(self):
        st = scheduler.wallclock()

        def task():
            while True:
                try:
                    yield
                except scheduler.Timeout:
                    return

        self.mox.StubOutWithMock(scheduler, 'wallclock')
        scheduler.wallclock().AndReturn(st)
        scheduler.wallclock().AndReturn(st + 0.5)
        scheduler.wallclock().AndReturn(st + 1.5)

        self.mox.ReplayAll()

        runner = scheduler.TaskRunner(task)

        runner.start(timeout=1)
        self.assertTrue(runner)
        self.assertTrue(runner.step())
        self.assertFalse(runner)

        self.mox.VerifyAll()

    def test_timeout_swallowed(self):
        st = scheduler.wallclock()

        def task():
            while True:
                try:
                    yield
                except scheduler.Timeout:
                    yield
                    self.fail('Task still running')

        self.mox.StubOutWithMock(scheduler, 'wallclock')
        scheduler.wallclock().AndReturn(st)
        scheduler.wallclock().AndReturn(st + 0.5)
        scheduler.wallclock().AndReturn(st + 1.5)

        self.mox.ReplayAll()

        runner = scheduler.TaskRunner(task)

        runner.start(timeout=1)
        self.assertTrue(runner)
        self.assertTrue(runner.step())
        self.assertFalse(runner)
        self.assertTrue(runner.step())

        self.mox.VerifyAll()
