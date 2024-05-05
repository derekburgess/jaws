FROM neo4j:latest

ARG NEO4J_USERNAME
ARG NEO4J_PASSWORD

ENV NEO4J_AUTH=${NEO4J_USERNAME}/${NEO4J_PASSWORD}
ENV NEO4J_initial_dbms_default__database=captures

EXPOSE 7474 7473 7687

CMD ["neo4j"]

# docker build -t jaws-neodbms --build-arg NEO4J_USERNAME --build-arg NEO4J_PASSWORD .
# docker run --name captures -p 7474:7474 -p 7687:7687 jaws-neodbms