"""Optional socket-based load of the isolated lab; start load_backend.py --serve first.

Run only against the explicitly started 127.0.0.1 mock lab. Native/paid capacity is
not measured. The primary ladder runner owns reproducible stop/resource evidence.
"""
from urllib.parse import urlsplit

from locust import HttpUser, between, events, task


@events.test_start.add_listener
def require_loopback(environment, **kwargs):
    parsed=urlsplit(environment.host or '')
    if parsed.scheme!='http' or parsed.hostname not in {'127.0.0.1','localhost'}:
        raise ValueError('Locust evaluation may target only the explicitly owned loopback mock lab')


class MockCallUser(HttpUser):
    wait_time=between(.05,.1)

    @task
    def full_text_call(self):
        with self.client.post('/lab/call',name='full_mock_text_call',catch_response=True) as response:
            if response.status_code!=200:
                response.failure('backend_error')
            elif response.json().get('quality_ok') is not True:
                response.failure('quality_failure')
