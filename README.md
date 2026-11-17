Overview
========

Welcome to Astronomer! This project was generated after you ran 'astro dev init' using the Astronomer CLI. This readme describes the contents of the project, as well as how to run Apache Airflow on your local machine.

Project Contents
================

Your Astro project contains the following files and folders:

- dags: This folder contains the Python files for your Airflow DAGs. By default, this directory includes two example DAGs:
    - `example_dag_basic`: This DAG shows a simple ETL data pipeline example with three TaskFlow API tasks that run daily.
    - `example_dag_advanced`: This advanced DAG showcases a variety of Airflow features like branching, Jinja templates, task groups and several Airflow operators.
- Dockerfile: This file contains a versioned Astro Runtime Docker image that provides a differentiated Airflow experience. If you want to execute other commands or overrides at runtime, specify them here.
- include: This folder contains any additional files that you want to include as part of your project. It is empty by default.
- packages.txt: Install OS-level packages needed for your project by adding them to this file. It is empty by default.
- requirements.txt: Install Python packages needed for your project by adding them to this file. It is empty by default.
- plugins: Add custom or community plugins for your project to this file. It is empty by default.
- airflow_settings.yaml: Use this local-only file to specify Airflow Connections, Variables, and Pools instead of entering them in the Airflow UI as you develop DAGs in this project.

Deploy Your Project Locally
===========================

1. Start Airflow on your local machine by running 'astro dev start'.

This command will spin up 4 Docker containers on your machine, each for a different Airflow component:

- Postgres: Airflow's Metadata Database
- Webserver: The Airflow component responsible for rendering the Airflow UI
- Scheduler: The Airflow component responsible for monitoring and triggering tasks
- Triggerer: The Airflow component responsible for triggering deferred tasks

2. Verify that all 4 Docker containers were created by running 'docker ps'.

Note: Running 'astro dev start' will start your project with the Airflow Webserver exposed at port 8080 and Postgres exposed at port 5432. If you already have either of those ports allocated, you can either stop your existing Docker containers or change the port.

3. Access the Airflow UI for your local Airflow project. To do so, go to http://localhost:8080/ and log in with 'admin' for both your Username and Password.

You should also be able to access your Postgres Database at 'localhost:5432/postgres'.

Develop in a Dev Container
==========================

This project includes a VS Code Dev Container with the Astro CLI, OpenCode
CLI, Docker CLI, Python 3.14, and the GitHub Copilot and Python extensions.
Install and start Docker Desktop, then open this project in VS Code and choose
"Rebuild and Reopen in Container" from the Command Palette. Sign in to GitHub
Copilot when prompted. Run `opencode` from the container terminal to use
OpenCode on the mounted workspace; configure its model/provider credentials
inside the container as needed. Python 3.14 is the editor interpreter; the
Astro Runtime used by `astro dev start` has its own Python version. Run
`astro dev start` in the container terminal. VS Code forwards the Airflow UI
on port 8080 and Postgres on port 5432.

Oracle NoSQL push
=================

The `jobserve_new_jobs` DAG ends with a `push_to_nosql` task that upserts each
new hashed role into the `jobserve_seen` table. The row key is `uid` and the
DAG's `hash` is stored as `content_hash`; the full job is stored in `document`.

Requirements and credentials:

- `oci>=2.150,<3` is declared in `requirements.txt` (rebuild the image after
  changing it).
- Credentials are resolved in this order: Airflow Connection `oci_nosql`,
  environment variables (the gitignored `.env` file locally), then a standard
  OCI config file if one is available in the Airflow environment. No Docker
  Compose override or mounted key file is needed.
- For local development, add the following to `.env`. The PEM private key can
  be pasted with real newlines or with literal `\n` characters:

  ```dotenv
  OCI_USER=ocid1.user.oc1..xxxxxxxx
  OCI_FINGERPRINT=xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx
  OCI_TENANCY=ocid1.tenancy.oc1..xxxxxxxx
  OCI_REGION=us-phoenix-1
  OCI_PRIVATE_KEY=-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----
  OCI_NOSQL_COMPARTMENT_ID=ocid1.tenancy.oc1..xxxxxxxx
  OCI_NOSQL_TABLE=jobserve_seen
  ```

- Alternatively, create an Airflow Connection with id `oci_nosql`. Put these
  keys in its extra JSON: `user`, `fingerprint`, `tenancy`, `region`,
  `key_content`, `pass_phrase`, `compartment_id`, and `table_name`. The
  connection's `login` and `password` are also accepted as fallbacks for
  `user` and `key_content`. Airflow Assets are for data lineage/scheduling;
  use a Connection or `.env` for credentials.
- `OCI_NOSQL_CONNECTION_ID` can point to a different connection id.
  `OCI_USE_INSTANCE_PRINCIPAL=true` or `OCI_USE_RESOURCE_PRINCIPAL=true` can be
  used instead of an API key where the runtime provides that identity.

Deploy Your Project to Astronomer
=================================

If you have an Astronomer account, pushing code to a Deployment on Astronomer is simple. For deploying instructions, refer to Astronomer documentation: https://docs.astronomer.io/cloud/deploy-code/

Contact
=======

The Astronomer CLI is maintained with love by the Astronomer team. To report a bug or suggest a change, reach out to our support.
