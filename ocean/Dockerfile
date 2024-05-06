FROM nvidia/cuda:12.4.1-devel-ubuntu22.04

ARG NEO4J_URI
ARG NEO4J_USERNAME
ARG NEO4J_PASSWORD
ARG IPINFO_API_KEY
ARG OPENAI_API_KEY
ARG HUGGINGFACE_API_KEY

ENV NEO4J_URI=${NEO4J_URI}
ENV NEO4J_USERNAME=${NEO4J_USERNAME}
ENV NEO4J_PASSWORD=${NEO4J_PASSWORD}
ENV IPINFO_API_KEY=${IPINFO_API_KEY}
ENV OPENAI_API_KEY=${OPENAI_API_KEY}
ENV HUGGINGFACE_API_KEY=${HUGGINGFACE_API_KEY}
ENV JAWS_FINDER_ENDPOINT=/home
# This does not use the host systems JAWS_FINDER_ENDPOINT, but instead uses the container's /home directory

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    git \
    tshark

WORKDIR /jaws

RUN git clone https://github.com/derekburgess/jaws.git .
RUN pip install -r requirements.txt
RUN pip install -i https://pypi.org/simple/ bitsandbytes
RUN pip install .

CMD ["tail", "-f", "/dev/null"]