"""AWS Lambda entry point for the read-only kernel API.

Mangum adapts the ASGI app in ``deploy.api`` to the Lambda proxy event shape.
The package is built by CI (``.github/workflows/deploy.yml``) from the same
source tree the tests run against, so a deployed build is a tested build.
"""
from mangum import Mangum

from deploy.api import app

handler = Mangum(app, lifespan="off")
