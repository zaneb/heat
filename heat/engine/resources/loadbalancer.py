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

from heat.engine import clients
from heat.common import exception
from heat.common import template_format
from heat.engine import stack_resource

from heat.openstack.common import log as logging

logger = logging.getLogger(__name__)

lb_template = r'''
{
  "AWSTemplateFormatVersion": "2010-09-09",
  "Description": "Built in HAProxy server",
  "Parameters" : {
    "KeyName" : {
      "Type" : "String"
    }
  },
  "Resources": {
    "latency_watcher": {
     "Type": "AWS::CloudWatch::Alarm",
     "Properties": {
        "MetricName": "Latency",
        "Namespace": "AWS/ELB",
        "Statistic": "Average",
        "Period": "60",
        "EvaluationPeriods": "1",
        "Threshold": "2",
        "AlarmActions": [],
        "ComparisonOperator": "GreaterThanThreshold"
      }
    },
    "CfnLBUser" : {
      "Type" : "AWS::IAM::User"
    },
    "CfnLBAccessKey" : {
      "Type" : "AWS::IAM::AccessKey",
      "Properties" : {
        "UserName" : {"Ref": "CfnLBUser"}
      }
    },
    "LB_instance": {
      "Type": "AWS::EC2::Instance",
      "Metadata": {
        "AWS::CloudFormation::Init": {
          "config": {
            "packages": {
              "yum": {
                "cronie"         : [],
                "haproxy"        : [],
                "socat"          : [],
                "python-psutil"  : []
              }
            },
            "services": {
              "systemd": {
                "crond"     : { "enabled" : "true", "ensureRunning" : "true" },
                "haproxy"   : { "enabled": "true", "ensureRunning": "true" }
              }
            },
            "files": {
              "/etc/cfn/cfn-credentials" : {
                "content" : { "Fn::Join" : ["", [
                  "AWSAccessKeyId=", { "Ref" : "CfnLBAccessKey" }, "\n",
                  "AWSSecretKey=", {"Fn::GetAtt": ["CfnLBAccessKey",
                                    "SecretAccessKey"]}, "\n"
                ]]},
                "mode"    : "000400",
                "owner"   : "root",
                "group"   : "root"
              },
              "/etc/cfn/cfn-hup.conf" : {
                "content" : { "Fn::Join" : ["", [
                  "[main]\n",
                  "stack=", { "Ref" : "AWS::StackId" }, "\n",
                  "credential-file=/etc/cfn/cfn-credentials\n",
                  "region=", { "Ref" : "AWS::Region" }, "\n",
                  "interval=60\n"
                ]]},
                "mode"    : "000400",
                "owner"   : "root",
                "group"   : "root"
              },
              "/etc/cfn/hooks.conf" : {
                "content": { "Fn::Join" : ["", [
                  "[cfn-init]\n",
                  "triggers=post.update\n",
                  "path=Resources.LB_instance.Metadata\n",
                  "action=/opt/aws/bin/cfn-init -s ",
                  { "Ref": "AWS::StackId" },
                  "    -r LB_instance ",
                  "    --region ", { "Ref": "AWS::Region" }, "\n",
                  "runas=root\n",
                  "\n",
                  "[reload]\n",
                  "triggers=post.update\n",
                  "path=Resources.LB_instance.Metadata\n",
                  "action=systemctl reload haproxy.service\n",
                  "runas=root\n"
                ]]},
                "mode"    : "000400",
                "owner"   : "root",
                "group"   : "root"
              },
              "/etc/haproxy/haproxy.cfg": {
                "content": "",
                "mode": "000644",
                "owner": "root",
                "group": "root"
              },
              "/tmp/cfn-hup-crontab.txt" : {
                "content" : { "Fn::Join" : ["", [
                "MAIL=\"\"\n",
                "\n",
                "* * * * * /opt/aws/bin/cfn-hup -f\n",
                "* * * * * /opt/aws/bin/cfn-push-stats ",
                " --watch ", { "Ref" : "latency_watcher" }, " --haproxy\n"
                ]]},
                "mode"    : "000600",
                "owner"   : "root",
                "group"   : "root"
              }
            }
          }
        }
      },
      "Properties": {
        "ImageId": "F17-x86_64-cfntools",
        "InstanceType": "m1.small",
        "KeyName": { "Ref": "KeyName" },
        "UserData": { "Fn::Base64": { "Fn::Join": ["", [
          "#!/bin/bash -v\n",
          "# Helper function\n",
          "function error_exit\n",
          "{\n",
          "  /opt/aws/bin/cfn-signal -e 1 -r \"$1\" '",
          { "Ref" : "WaitHandle" }, "'\n",
          "  exit 1\n",
          "}\n",

          "/opt/aws/bin/cfn-init -s ",
          { "Ref": "AWS::StackId" },
          "    -r LB_instance ",
          "    --region ", { "Ref": "AWS::Region" }, "\n",
          "# install cfn-hup crontab\n",
          "crontab /tmp/cfn-hup-crontab.txt\n",

          "# LB setup completed, signal success\n",
          "/opt/aws/bin/cfn-signal -e 0 -r \"LB server setup complete\" '",
          { "Ref" : "WaitHandle" }, "'\n"

        ]]}}
      }
    },
    "WaitHandle" : {
      "Type" : "AWS::CloudFormation::WaitConditionHandle"
    },

    "WaitCondition" : {
      "Type" : "AWS::CloudFormation::WaitCondition",
      "DependsOn" : "LB_instance",
      "Properties" : {
        "Handle" : {"Ref" : "WaitHandle"},
        "Timeout" : "600"
      }
    }
  },

  "Outputs": {
    "PublicIp": {
      "Value": { "Fn::GetAtt": [ "LB_instance", "PublicIp" ] },
      "Description": "instance IP"
    }
  }
}
'''


#
# TODO(asalkeld) the above inline template _could_ be placed in an external
# file at the moment this is because we will probably need to implement a
# LoadBalancer based on keepalived as well (for for ssl support).
#
class LoadBalancer(stack_resource.StackResource):

    listeners_schema = {
        'InstancePort': {'Type': 'Number',
                         'Required': True},
        'LoadBalancerPort': {'Type': 'Number',
                             'Required': True},
        'Protocol': {'Type': 'String',
                     'Required': True,
                     'AllowedValues': ['TCP', 'HTTP']},
        'SSLCertificateId': {'Type': 'String',
                             'Implemented': False},
        'PolicyNames': {'Type': 'List',
                        'Implemented': False}
    }
    healthcheck_schema = {
        'HealthyThreshold': {'Type': 'Number',
                             'Required': True},
        'Interval': {'Type': 'Number',
                     'Required': True},
        'Target': {'Type': 'String',
                   'Required': True},
        'Timeout': {'Type': 'Number',
                    'Required': True},
        'UnhealthyThreshold': {'Type': 'Number',
                               'Required': True},
    }

    properties_schema = {
        'AvailabilityZones': {'Type': 'List',
                              'Required': True},
        'HealthCheck': {'Type': 'Map',
                        'Schema': healthcheck_schema},
        'Instances': {'Type': 'List'},
        'Listeners': {'Type': 'List',
                      'Schema': {'Type': 'Map',
                                 'Schema': listeners_schema}},
        'AppCookieStickinessPolicy': {'Type': 'String',
                                      'Implemented': False},
        'LBCookieStickinessPolicy': {'Type': 'String',
                                     'Implemented': False},
        'SecurityGroups': {'Type': 'String',
                           'Implemented': False},
        'Subnets': {'Type': 'List',
                    'Implemented': False}
    }

    def _instance_to_ipaddress(self, inst):
        '''
        Return the server's IP address, fetching it from Nova
        '''
        try:
            server = self.nova().servers.get(inst)
        except clients.novaclient.exceptions.NotFound as ex:
            logger.warn('Instance (%s) not found: %s' % (inst, str(ex)))
        else:
            for n in server.networks:
                return server.networks[n][0]

        return '0.0.0.0'

    def _haproxy_config(self, templ, instances):
        # initial simplifications:
        # - only one Listener
        # - only http (no tcp or ssl)
        #
        # option httpchk HEAD /check.txt HTTP/1.0
        gl = '''
    global
        daemon
        maxconn 256
        stats socket /tmp/.haproxy-stats

    defaults
        mode http
        timeout connect 5000ms
        timeout client 50000ms
        timeout server 50000ms
'''

        listener = self.properties['Listeners'][0]
        lb_port = listener['LoadBalancerPort']
        inst_port = listener['InstancePort']
        spaces = '            '
        frontend = '''
        frontend http
            bind *:%s
''' % (lb_port)

        health_chk = self.properties['HealthCheck']
        if health_chk:
            check = 'check inter %ss fall %s rise %s' % (
                    health_chk['Interval'],
                    health_chk['UnhealthyThreshold'],
                    health_chk['HealthyThreshold'])
            timeout_check = 'timeout check %ds' % int(health_chk['Timeout'])
        else:
            check = ''
            timeout_check = ''

        backend = '''
        default_backend servers

        backend servers
            balance roundrobin
            option http-server-close
            option forwardfor
            option httpchk
            %s
''' % timeout_check

        servers = []
        n = 1
        for i in instances:
            ip = self._instance_to_ipaddress(i)
            logger.debug('haproxy server:%s' % ip)
            servers.append('%sserver server%d %s:%s %s' % (spaces, n,
                                                           ip, inst_port,
                                                           check))
            n = n + 1

        return '%s%s%s%s\n' % (gl, frontend, backend, '\n'.join(servers))

    def handle_create(self):
        templ = template_format.parse(lb_template)

        if self.properties['Instances']:
            md = templ['Resources']['LB_instance']['Metadata']
            files = md['AWS::CloudFormation::Init']['config']['files']
            cfg = self._haproxy_config(templ, self.properties['Instances'])
            files['/etc/haproxy/haproxy.cfg']['content'] = cfg

        # total hack - probably need an admin key here.
        param = self.stack.resolve_static_data({'KeyName': {'Ref': 'KeyName'}})

        return self.create_with_template(templ, param)

    def handle_update(self, json_snippet):
        return self.UPDATE_REPLACE

    def handle_delete(self):
        self.delete_nested()

    def validate(self):
        '''
        Validate any of the provided params
        '''
        res = super(LoadBalancer, self).validate()
        if res:
            return res

        health_chk = self.properties['HealthCheck']
        if health_chk:
            if float(health_chk['Interval']) < float(health_chk['Timeout']):
                return {'Error':
                        'Interval must be larger than Timeout'}

    def reload(self, inst_list):
        '''
        re-generate the Metadata
        save it to the db.
        rely on the cfn-hup to reconfigure HAProxy
        '''
        templ = template_format.parse(lb_template)
        cfg = self._haproxy_config(templ, inst_list)

        md = self.nested()['LB_instance'].metadata
        files = md['AWS::CloudFormation::Init']['config']['files']
        files['/etc/haproxy/haproxy.cfg']['content'] = cfg

        self.nested()['LB_instance'].metadata = md

    def FnGetRefId(self):
        return unicode(self.name)

    def FnGetAtt(self, key):
        '''
        We don't really support any of these yet.
        '''
        allow = ('CanonicalHostedZoneName',
                 'CanonicalHostedZoneNameID',
                 'DNSName',
                 'SourceSecurityGroupName',
                 'SourceSecurityGroupOwnerAlias')

        if not key in allow:
            raise exception.InvalidTemplateAttribute(resource=self.name,
                                                     key=key)

        if key == 'DNSName':
            return self.get_output('PublicIp')
        else:
            return ''


def resource_mapping():
    return {
        'AWS::ElasticLoadBalancing::LoadBalancer': LoadBalancer,
    }
