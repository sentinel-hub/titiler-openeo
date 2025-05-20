ARG PYTHON_VERSION=3.11

FROM public.ecr.aws/lambda/python:${PYTHON_VERSION}

# Install system dependencies to compile (numexpr)
RUN yum install -y gcc-c++

WORKDIR /tmp


# Install system dependencies to compile (numexpr)
COPY titiler/ /tmp/app/titiler
COPY pyproject.toml /tmp/app/pyproject.toml
COPY README.md /tmp/app/README.md
COPY LICENSE /tmp/app/LICENSE

# Install dependencies
RUN pip install --upgrade pip
# https://github.com/developmentseed/titiler-md-demo/issues/3 if using python 3.12
RUN pip install /tmp/app "mangum>=0.10.0" -t /asset

RUN rm -rdf /tmp/app

# Reduce package size and remove useless files
RUN cd /asset && find . -type f -name '*.pyc' | while read f; do n=$(echo $f | sed 's/__pycache__\///' | sed 's/.cpython-[0-9]*//'); cp $f $n; done;
RUN cd /asset && find . -type d -a -name '__pycache__' -print0 | xargs -0 rm -rf
RUN cd /asset && find . -type f -a -name '*.py' -print0 | xargs -0 rm -f
RUN find /asset -type d -a -name 'tests' -print0 | xargs -0 rm -rf
RUN rm -rdf /asset/numpy/doc/ /asset/bin /asset/geos_license /asset/Misc
RUN rm -rdf /asset/boto3*
RUN rm -rdf /asset/botocore*

COPY infrastructure/aws/lambda/handler.py /asset/handler.py

# Copy Services files
COPY services/copernicus.json /asset/services/copernicus.json
COPY services/eoapi.json /asset/services/eoapi.json

WORKDIR /asset

# Set the ENV to test the handler
ENV TITILER_OPENEO_STAC_API_URL="https://stac.eoapi.dev"
ENV TITILER_OPENEO_SERVICE_STORE_URL="/asset/services/eoapi.json"

RUN python -c "from handler import handler; print('All Good')"

CMD ["echo", "hello world"]
