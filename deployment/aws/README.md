## Deployments

The following steps detail how to to setup and deploy the CDK stack from your local machine.

1. Install CDK and connect to your AWS account. This step is only necessary once per AWS account.

```bash
# Download titiler repo
git clone https://github.com/sentinel-hub/titiler-openeo.git
cd titiler-openeo

# Create a virtual environment
python -m pip install --upgrade virtualenv
virtualenv infrastructure/aws/.venv
source infrastructure/aws/.venv/bin/activate

# install cdk dependencies
python -m pip install -r infrastructure/aws/requirements-cdk.txt

# Install node dependency
npm --prefix infrastructure/aws install

# Deploys the CDK toolkit stack into an AWS environment
npm --prefix infrastructure/aws run cdk -- bootstrap

# or to a specific region and or using AWS profile
AWS_DEFAULT_REGION=us-east-1 AWS_REGION=us-east-1 AWS_PROFILE=myprofile npm --prefix infrastructure/aws run cdk -- bootstrap
```

2. Update settings

Set environment variable or hard code in `infrastructure/aws/.env` file (e.g `STACK_STAGE=testing`).

NOTE: At the time of writing this readme, 2 environment variables are mandatory in order to use the API:

- TITILER_OPENEO_STAC_API_URL ("https://stac.eoapi.dev" or "https://stac.dataspace.copernicus.eu/v1")
- TITILER_OPENEO_STORE_URL ("/var/task/services/eoapi.json" or "/var/task/services/copernicus.json")

3. Pre-Generate CFN template

```bash
npm --prefix infrastructure/aws run cdk -- synth  # Synthesizes and prints the CloudFormation template for this stack
```

4. Deploy

```bash
STACK_STAGE=staging npm --prefix infrastructure/aws run cdk -- deploy titiler-openeo-staging

# Deploy in specific region
AWS_DEFAULT_REGION=us-west-2 AWS_REGION=us-west-2 AWS_PROFILE=prof npm --prefix infrastructure/aws run cdk -- deploy titiler-openeo-production
```
