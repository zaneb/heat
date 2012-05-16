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

import eventlet
import logging
import json
import os

from heat.common import exception
from heat.db import api as db_api
from heat.engine.resources import Resource

logger = logging.getLogger('heat.endine.cloud_watch')


class CloudWatchAlarm(Resource):
    required_props = ['ComparisonOperator', 'EvaluationPeriods',
                      'MetricName', 'Namespace', 'Period', 'Statistic',
                      'Threshold']
    validation = {
        'Statistic': ['SampleCount', 'Average', 'Sum', 'Minimum', 'Maximum'],
        'Units': ['Seconds', 'Microseconds', 'Milliseconds',
                  'Bytes', 'Kilobytes', 'Megabytes', 'Gigabytes',
                  'Terabytes', 'Bits', 'Kilobits', 'Megabits', 'Gigabits',
                  'Terabits', 'Percent', 'Count', 'Bytes/Second',
                  'Kilobytes/Second', 'Megabytes/Second', 'Gigabytes/Second',
                  'Terabytes/Second', 'Bits/Second', 'Kilobits/Second',
                  'Megabits/Second', 'Gigabits/Second', 'Terabits/Second',
                  'Count/Second', None],
        'ComparisonOperator': ['GreaterThanOrEqualToThreshold',
                               'GreaterThanThreshold',
                               'LessThanThreshold',
                               'LessThanOrEqualToThreshold']
    }

    def __init__(self, name, json_snippet, stack):
        super(CloudWatchAlarm, self).__init__(name, json_snippet, stack)
        self.instance_id = ''

    def validate(self):
        '''
        Validate the Properties
        '''
        for r in self.required_props:
            if not r in self.t['Properties']:
                    return {'Error': \
                    '%s is a required Property of CloudWatch' % {r}}
        for p in self.t['Properties']:
            if p in self.validation:
                if not self.t['Properties'][p] in self.validation[p]:
                    return {'Error': \
                    '%s in not a valid option to %s' % \
                    {self.t['Properties'][p], p}}
        # EvaluationPeriods > 0
        return None


    def create(self):
        if self.state != None:
            return
        self.state_set(self.CREATE_IN_PROGRESS)
        Resource.create(self)

        wr_values = {
            'name': self.name,
            'rule': self.t['Properties'],
            'state': 'NORMAL',
            'stack_name': self.stack.name
        }

        wr = db_api.watch_rule_create(None, wr_values)
        self.instance_id = wr.id

        self.state_set(self.CREATE_COMPLETE)

    def delete(self):
        if self.state == self.DELETE_IN_PROGRESS or \
           self.state == self.DELETE_COMPLETE:
            return

        self.state_set(self.DELETE_IN_PROGRESS)

        db_api.watch_rule_delete(None, self.name)
        db_api.watch_data_delete(None, self.name)

        Resource.delete(self)
        self.state_set(self.DELETE_COMPLETE)

    def FnGetRefId(self):
        return unicode(self.name)

