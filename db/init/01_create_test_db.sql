-- Runs once when the postgres container first initializes its data volume.
-- POSTGRES_DB (reviewme) is created automatically by the entrypoint;
-- here we additionally create the test database.
CREATE DATABASE reviewme_test;
