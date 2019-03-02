# -*- coding: utf-8 -*-

# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
"""Gnocchi collectd plugin implementation"""

from __future__ import division
from __future__ import unicode_literals

import collectd_openstack
from collectd_openstack.common import sender as common_sender
from collectd_openstack.common.settings import Config

import json
import logging

LOGGER = logging.getLogger(__name__)
ROOT_LOGGER = logging.getLogger(collectd_openstack.__name__)


class Sender(common_sender.Sender):
    """Sends the JSON serialized data to Gnocchi"""

    def __init__(self, meter_type):
        """Create the Sender instance.

        The configuration must be initialized before the object is created.
        """
        super(Sender, self).__init__()
        self._meter_ids = {}
        self.meter_type = meter_type

    def _on_authenticated(self):
        # get the uri of service endpoint
        self.region = self._get_region()
        endpoint = self._get_endpoint("gnocchi", self.region)
        node_uuid = self._get_node_uuid()
        self._url_base = "{}/v1/metric/%s/measures".format(endpoint)

        self.resource_id = self._get_resource(node_uuid, endpoint)
        if self.resource_id is None:
            LOGGER.debug(
                "Resource %s does not exist, creating it now", node_uuid)
            self.resource_id = self._create_resource(node_uuid, endpoint)

    def _get_region(self):
        try:
            url = 'http://169.254.169.254/openstack/latest/vendor_data2.json'
            result = self._perform_request(
                url, None, self._auth_token, req_type="get")
            result_json = result.json()
            if 'chameleon' in result_json:
                result_json = result_json['chameleon']
            else:
                url = 'http://169.254.169.254/openstack/latest/vendor_data.json'
                result = self._perform_request(
                    url, None, self._auth_token, req_type="get")
                result_json = result.json()
            region = result_json['region']
        except Exception:
            region = None
        return region

    def _get_node_uuid(self):

        if self.meter_type == 'cuda':
            url = 'http://169.254.169.254/openstack/latest/vendor_data2.json'
            key = 'node'
        else:
            url = 'http://169.254.169.254/openstack/latest/meta_data.json'
            key = 'uuid'
        try:
            result = self._perform_request(
                url, None, self._auth_token, req_type="get")
            result_json = result.json()
            if key == 'node':
                if 'chameleon' in result_json:
                    result_json = result_json['chameleon']
                else:
                    result = self._perform_request(
                        'http://169.254.169.254/openstack/latest/vendor_data.json', None, self._auth_token, req_type="get")
                    result_json = result.json()
            node_uuid = result_json[key]
            LOGGER.debug("node_uuid=%s", node_uuid)
        except Exception:
            node_uuid = None
        return node_uuid

    def _create_request_url(self, metername, **kwargs):
        unit = kwargs['unit']
        metric_id = self._get_metric_id(metername, unit)
        return self._url_base % (metric_id)

    def _handle_http_error(self, exc, metername,
                           payload, auth_token, **kwargs):
        response = exc.response
        if response.status_code == common_sender.Sender.HTTP_NOT_FOUND:
            LOGGER.debug("Received 404 error when submitting %s sample, \
                         creating a new metric",
                         metername)

            # create metric (endpoint, metername)
            unit = kwargs['unit']
            metric_id = self._get_metric_id(metername, unit)

            LOGGER.info('metername: %s, meter_id: %s', metername, metric_id)
            # Set a new url for the request
            url = self._url_base % (metric_id)
            # TODO(emma-l-foley): Add error checking
            # Submit the sample
            result = self._perform_request(url, payload, auth_token)
            if result.status_code == common_sender.Sender.HTTP_CREATED:
                LOGGER.debug('Result: %s', common_sender.Sender.HTTP_CREATED)
            else:
                LOGGER.info('Result: %s %s',
                            result.status_code,
                            result.text)

        else:
            raise exc

    def _get_endpoint(self, service, region=None):
        # get the uri of service endpoint
        endpoint = self._keystone.get_service_endpoint(
            service,
            Config.instance().CEILOMETER_URL_TYPE,
            region=region)
        return endpoint

    def _get_metric_id(self, metername, unit):

        try:
            return self._meter_ids[metername]
        except KeyError as ke:
            LOGGER.warn(ke)
            LOGGER.warn('No known ID for %s', metername)

            endpoint = self._get_endpoint("gnocchi", region=self.region)
            metric_id = self._get_metric(metername, endpoint, unit)
            if metric_id is not None:
                self._meter_ids[metername] = metric_id
            else:
                self._meter_ids[metername] = self._create_metric(
                    metername, endpoint, unit)

        return self._meter_ids[metername]

    def _get_metric(self, metername, endpoint, unit):
        if self.resource_id is None:
            return None

        url = "{}/v1/resource/{}/{}/metric/".format(
            endpoint, self.meter_type, self.resource_id)
        try:
            result = self._perform_request(
                url, None, self._auth_token, req_type="get")
            metrics = json.loads(result.text)
            metrics = [
                m for m in metrics
                if m['name'] == metername and m['unit'] == unit]
            metric_id = metrics[0]['id']
        except Exception:
            metric_id = None
        return metric_id

    def _create_metric(self, metername, endpoint, unit):
        if self.resource_id is None:
            return None

        url = "{}/v1/resource/{}/{}/metric/".format(
            endpoint, self.meter_type, self.resource_id)
        payload = json.dumps(
            {metername: {"archive_policy_name": "high", "unit": unit}})
        result = self._perform_request(url, payload, self._auth_token)
        metric_id = json.loads(result.text)['id']
        LOGGER.debug("metric_id=%s", metric_id)
        return metric_id

    def _get_resource(self, resource_id, endpoint):
        url = "{}/v1/resource/{}/{}".format(
            endpoint, self.meter_type, resource_id)
        try:
            result = self._perform_request(
                url, None, self._auth_token, req_type="get")
            resource_id = json.loads(result.text)['id']
            LOGGER.debug("resource_id=%s", resource_id)
        except Exception:
            resource_id = None
        return resource_id

    def _create_resource(self, resource_id, endpoint):
        url = "{}/v1/resource/{}".format(endpoint, self.meter_type)
        payload = json.dumps({"id": resource_id})
        try:
            result = self._perform_request(url, payload, self._auth_token)
            resource_id = json.loads(result.text)['id']
            LOGGER.debug("resource_id=%s", resource_id)
        except Exception:
            resource_id = None
        return resource_id
