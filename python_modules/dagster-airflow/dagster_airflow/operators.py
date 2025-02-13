'''The dagster-airflow operators.'''
import ast
import json
import logging
import os

from contextlib import contextmanager

from airflow.exceptions import AirflowException, AirflowSkipException
from airflow.operators.docker_operator import DockerOperator
from airflow.operators.python_operator import PythonOperator
from airflow.utils.file import TemporaryDirectory
from docker import APIClient, from_env

from dagster import check, seven, DagsterEventType
from dagster.core.events import DagsterEvent
from dagster_graphql.client.mutations import execute_start_pipeline_execution_query
from dagster_graphql.client.query import START_PIPELINE_EXECUTION_QUERY

from .util import airflow_storage_exception, construct_variables, parse_raw_res


DOCKER_TEMPDIR = '/tmp'

DEFAULT_ENVIRONMENT = {
    'AWS_ACCESS_KEY_ID': os.getenv('AWS_ACCESS_KEY_ID'),
    'AWS_SECRET_ACCESS_KEY': os.getenv('AWS_SECRET_ACCESS_KEY'),
}

LINE_LENGTH = 100


def skip_self_if_necessary(events):
    '''Using AirflowSkipException is a canonical way for tasks to skip themselves; see example
    here: http://bit.ly/2YtigEm
    '''
    check.list_param(events, 'events', of_type=DagsterEvent)

    skipped = any([e.event_type_value == DagsterEventType.STEP_SKIPPED.value for e in events])

    if skipped:
        raise AirflowSkipException('Dagster emitted skip event, skipping execution in Airflow')


class ModifiedDockerOperator(DockerOperator):
    """ModifiedDockerOperator supports host temporary directories on OSX.

    Incorporates https://github.com/apache/airflow/pull/4315/ and an implementation of
    https://issues.apache.org/jira/browse/AIRFLOW-3825.

    :param host_tmp_dir: Specify the location of the temporary directory on the host which will
        be mapped to tmp_dir. If not provided defaults to using the standard system temp directory.
    :type host_tmp_dir: str
    """

    def __init__(self, host_tmp_dir='/tmp', **kwargs):
        self.host_tmp_dir = host_tmp_dir
        kwargs['xcom_push'] = True
        super(ModifiedDockerOperator, self).__init__(**kwargs)

    @contextmanager
    def get_host_tmp_dir(self):
        '''Abstracts the tempdir context manager so that this can be overridden.'''
        with TemporaryDirectory(prefix='airflowtmp', dir=self.host_tmp_dir) as tmp_dir:
            yield tmp_dir

    def execute(self, context):
        '''Modified only to use the get_host_tmp_dir helper.'''
        self.log.info('Starting docker container from image %s', self.image)

        tls_config = self.__get_tls_config()
        if self.docker_conn_id:
            self.cli = self.get_hook().get_conn()
        else:
            self.cli = APIClient(base_url=self.docker_url, version=self.api_version, tls=tls_config)

        if self.force_pull or len(self.cli.images(name=self.image)) == 0:
            self.log.info('Pulling docker image %s', self.image)
            for l in self.cli.pull(self.image, stream=True):
                output = json.loads(l.decode('utf-8').strip())
                if 'status' in output:
                    self.log.info("%s", output['status'])

        with self.get_host_tmp_dir() as host_tmp_dir:
            self.environment['AIRFLOW_TMP_DIR'] = self.tmp_dir
            self.volumes.append('{0}:{1}'.format(host_tmp_dir, self.tmp_dir))

            self.container = self.cli.create_container(
                command=self.get_command(),
                environment=self.environment,
                host_config=self.cli.create_host_config(
                    auto_remove=self.auto_remove,
                    binds=self.volumes,
                    network_mode=self.network_mode,
                    shm_size=self.shm_size,
                    dns=self.dns,
                    dns_search=self.dns_search,
                    cpu_shares=int(round(self.cpus * 1024)),
                    mem_limit=self.mem_limit,
                ),
                image=self.image,
                user=self.user,
                working_dir=self.working_dir,
            )
            self.cli.start(self.container['Id'])

            res = []
            line = ''
            for new_line in self.cli.logs(container=self.container['Id'], stream=True):
                line = new_line.strip()
                if hasattr(line, 'decode'):
                    line = line.decode('utf-8')
                self.log.info(line)
                res.append(line)

            result = self.cli.wait(self.container['Id'])
            if result['StatusCode'] != 0:
                raise AirflowException('docker container failed: ' + repr(result))

            if self.xcom_push_flag:
                # Try to avoid any kind of race condition?
                return '\n'.join(res) + '\n' if self.xcom_all else str(line)

    # This is a class-private name on DockerOperator for no good reason --
    # all that the status quo does is inhibit extension of the class.
    # See https://issues.apache.org/jira/browse/AIRFLOW-3880
    def __get_tls_config(self):
        # pylint: disable=no-member
        return super(ModifiedDockerOperator, self)._DockerOperator__get_tls_config()


class DagsterDockerOperator(ModifiedDockerOperator):
    '''Dagster operator for Apache Airflow.

    Wraps a modified DockerOperator incorporating https://github.com/apache/airflow/pull/4315.

    Additionally, if a Docker client can be initialized using docker.from_env,
    Unlike the standard DockerOperator, this operator also supports config using docker.from_env,
    so it isn't necessary to explicitly set docker_url, tls_config, or api_version.

    '''

    # py2 compat
    # pylint: disable=keyword-arg-before-vararg
    def __init__(
        self,
        task_id,
        environment_dict=None,
        pipeline_name=None,
        mode=None,
        step_keys=None,
        dag=None,
        *args,
        **kwargs
    ):
        check.str_param(pipeline_name, 'pipeline_name')
        step_keys = check.opt_list_param(step_keys, 'step_keys', of_type=str)
        environment_dict = check.opt_dict_param(environment_dict, 'environment_dict', key_type=str)

        tmp_dir = kwargs.pop('tmp_dir', DOCKER_TEMPDIR)
        host_tmp_dir = kwargs.pop('host_tmp_dir', seven.get_system_temp_directory())

        if 'storage' not in environment_dict:
            raise airflow_storage_exception(tmp_dir)

        check.invariant(
            'in_memory' not in environment_dict.get('storage', {}),
            'Cannot use in-memory storage with Airflow, must use S3',
        )

        self.docker_conn_id_set = kwargs.get('docker_conn_id') is not None
        self.environment_dict = environment_dict
        self.pipeline_name = pipeline_name
        self.mode = mode
        self.step_keys = step_keys
        self._run_id = None

        # These shenanigans are so we can override DockerOperator.get_hook in order to configure
        # a docker client using docker.from_env, rather than messing with the logic of
        # DockerOperator.execute
        if not self.docker_conn_id_set:
            try:
                from_env().version()
            except Exception:  # pylint: disable=broad-except
                pass
            else:
                kwargs['docker_conn_id'] = True

        # We do this because log lines won't necessarily be emitted in order (!) -- so we can't
        # just check the last log line to see if it's JSON.
        kwargs['xcom_all'] = True

        # Store Airflow DAG run timestamp so that we can pass along via execution metadata
        self.airflow_ts = kwargs.get('ts')

        if 'environment' not in kwargs:
            kwargs['environment'] = DEFAULT_ENVIRONMENT

        super(DagsterDockerOperator, self).__init__(
            task_id=task_id, dag=dag, tmp_dir=tmp_dir, host_tmp_dir=host_tmp_dir, *args, **kwargs
        )

    @property
    def run_id(self):
        if self._run_id is None:
            return ''
        else:
            return self._run_id

    @property
    def query(self):
        # TODO: https://github.com/dagster-io/dagster/issues/1342
        redacted = construct_variables(
            self.mode, 'REDACTED', self.pipeline_name, self.run_id, self.airflow_ts, self.step_keys
        )
        self.log.info(
            'Executing GraphQL query: {query}\n'.format(query=START_PIPELINE_EXECUTION_QUERY)
            + 'with variables:\n'
            + seven.json.dumps(redacted, indent=2)
        )

        variables = construct_variables(
            self.mode,
            self.environment_dict,
            self.pipeline_name,
            self.run_id,
            self.airflow_ts,
            self.step_keys,
        )

        return '-v \'{variables}\' \'{query}\''.format(
            variables=seven.json.dumps(variables), query=START_PIPELINE_EXECUTION_QUERY
        )

    def get_command(self):
        if self.command is not None and self.command.strip().find('[') == 0:
            commands = ast.literal_eval(self.command)
        elif self.command is not None:
            commands = self.command
        else:
            commands = self.query
        return commands

    def get_hook(self):
        if self.docker_conn_id_set:
            return super(DagsterDockerOperator, self).get_hook()

        class _DummyHook(object):
            def get_conn(self):
                return from_env().api

        return _DummyHook()

    def execute(self, context):
        try:
            from dagster_graphql.client.mutations import (
                handle_start_pipeline_execution_errors,
                handle_start_pipeline_execution_result,
            )

        except ImportError:
            raise AirflowException(
                'To use the DagsterPythonOperator, dagster and dagster_graphql must be installed '
                'in your Airflow environment.'
            )
        if 'run_id' in self.params:
            self._run_id = self.params['run_id']
        elif 'dag_run' in context and context['dag_run'] is not None:
            self._run_id = context['dag_run'].run_id

        try:
            raw_res = super(DagsterDockerOperator, self).execute(context)
            self.log.info('Finished executing container.')

            res = parse_raw_res(raw_res)

            handle_start_pipeline_execution_errors(res)
            events = handle_start_pipeline_execution_result(res)

            skip_self_if_necessary(events)

            return events

        finally:
            self._run_id = None

    # This is a class-private name on DockerOperator for no good reason --
    # all that the status quo does is inhibit extension of the class.
    # See https://issues.apache.org/jira/browse/AIRFLOW-3880
    def __get_tls_config(self):
        # pylint:disable=no-member
        return super(DagsterDockerOperator, self)._ModifiedDockerOperator__get_tls_config()

    @contextmanager
    def get_host_tmp_dir(self):
        yield self.host_tmp_dir


class DagsterPythonOperator(PythonOperator):
    def __init__(
        self,
        task_id,
        handle,
        pipeline_name,
        environment_dict,
        mode,
        step_keys,
        dag,
        *args,
        **kwargs
    ):
        if 'storage' not in environment_dict:
            raise airflow_storage_exception('/tmp/special_place')

        check.invariant(
            'in_memory' not in environment_dict.get('storage', {}),
            'Cannot use in-memory storage with Airflow, must use filesystem or S3',
        )

        def python_callable(ts, dag_run, **kwargs):  # pylint: disable=unused-argument
            run_id = dag_run.run_id

            # TODO: https://github.com/dagster-io/dagster/issues/1342
            redacted = construct_variables(mode, 'REDACTED', pipeline_name, run_id, ts, step_keys)
            logging.info(
                'Executing GraphQL query: {query}\n'.format(query=START_PIPELINE_EXECUTION_QUERY)
                + 'with variables:\n'
                + seven.json.dumps(redacted, indent=2)
            )
            events = execute_start_pipeline_execution_query(
                handle,
                construct_variables(mode, environment_dict, pipeline_name, run_id, ts, step_keys),
            )

            skip_self_if_necessary(events)

            return events

        super(DagsterPythonOperator, self).__init__(
            task_id=task_id,
            provide_context=True,
            python_callable=python_callable,
            dag=dag,
            *args,
            **kwargs
        )
