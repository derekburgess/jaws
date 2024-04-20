FROM neo4j:latest

ENV NEO4J_AUTH=neo4j/testtest

ENV NEO4J_dbms_default__database=captures

EXPOSE 7474 7473 7687

CMD ["neo4j"]
