from abc import ABCMeta, abstractmethod
from collections import namedtuple
import os
import pickle

import six

from dagster import check
from dagster.utils import mkdir_p


class StepOutputHandle(namedtuple('_StepOutputHandle', 'step_key output_name')):
    @staticmethod
    def from_step(step, output_name):
        from .objects import ExecutionStep

        check.inst_param(step, 'step', ExecutionStep)

        return StepOutputHandle(step.key, output_name)

    def __new__(cls, step_key, output_name):
        return super(StepOutputHandle, cls).__new__(
            cls,
            step_key=check.str_param(step_key, 'step_key'),
            output_name=check.str_param(output_name, 'output_name'),
        )


def read_pickle_file(path):
    check.str_param(path, 'path')
    with open(path, 'rb') as ff:
        return pickle.load(ff)


def write_pickle_file(path, value):
    check.str_param(path, 'path')
    with open(path, 'wb') as ff:
        return pickle.dump(value, ff)


class IntermediatesManager(six.with_metaclass(ABCMeta)):  # pylint: disable=no-init
    @abstractmethod
    def get_value(self, step_output_handle):
        pass

    @abstractmethod
    def set_value(self, step_output_handle, value):
        pass

    @abstractmethod
    def has_value(self, step_output_handle):
        pass


class InMemoryIntermediatesManager(IntermediatesManager):
    def __init__(self):
        self.values = {}

    def get_value(self, step_output_handle):
        check.inst_param(step_output_handle, 'step_output_handle', StepOutputHandle)
        return self.values[step_output_handle]

    def set_value(self, step_output_handle, value):
        check.inst_param(step_output_handle, 'step_output_handle', StepOutputHandle)
        self.values[step_output_handle] = value

    def has_value(self, step_output_handle):
        check.inst_param(step_output_handle, 'step_output_handle', StepOutputHandle)
        return step_output_handle in self.values


# TODO: This should go through persistence and serialization infrastructure
# Fixing this requires getting the type information (serialization_strategy is
# a property of runtime type) of the step output you are dealing with. As things
# are structured now we construct the inputs before execution. The fix to this
# will likely be to inject a different type of execution step that threads
# the manager
class FileSystemIntermediateManager(IntermediatesManager):
    def __init__(self, root):
        check.invariant(os.path.isdir(root))
        self._root = root

    @property
    def root(self):
        return self._root

    def _create_path_from_handle(self, step_output_handle):
        check.inst_param(step_output_handle, 'step_output_handle', StepOutputHandle)
        return os.path.join(self._root, step_output_handle.step_key, step_output_handle.output_name)

    def get_value(self, step_output_handle):
        check.inst_param(step_output_handle, 'step_output_handle', StepOutputHandle)
        check.invariant(self.has_value(step_output_handle))
        output_path = self._create_path_from_handle(step_output_handle)
        check.invariant(os.path.isfile(output_path))
        return read_pickle_file(output_path)

    def set_value(self, step_output_handle, value):
        check.inst_param(step_output_handle, 'step_output_handle', StepOutputHandle)
        check.invariant(not self.has_value(step_output_handle))

        mkdir_p('{root}/{step_key}'.format(root=self._root, step_key=step_output_handle.step_key))

        output_path = self._create_path_from_handle(step_output_handle)
        check.invariant(not os.path.exists(output_path))
        write_pickle_file(output_path, value)

    def has_value(self, step_output_handle):
        check.inst_param(step_output_handle, 'step_output_handle', StepOutputHandle)
        output_path = self._create_path_from_handle(step_output_handle)
        if os.path.exists(output_path):
            check.invariant(os.path.isfile(output_path))
            return True
        else:
            return False
