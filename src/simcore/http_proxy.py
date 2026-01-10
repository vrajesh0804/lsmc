import requests
from flask import request

def forward_to_localstack(localstack_url: str, method: str, path: str, timeout: int = 10):
    """
    Forward the incoming Flask request to LocalStack.
    """
    url = f"{localstack_url}/{path}"
    headers = {k: v for k, v in request.headers if k.lower() != "host"}
    return requests.request(
        method,
        url,
        headers=headers,
        data=request.get_data(),
        params=request.args,
        timeout=timeout,
    )
