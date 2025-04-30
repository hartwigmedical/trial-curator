# this import is necessary for the python code to load correctly
from trialcurator.criterion_schema import *

def exec_py_into_variable(py_code: str):
    ldic = locals()
    exec('var = ' + py_code, globals(), ldic)
    return ldic["var"]

def exec_file_into_variable(trial_curated_file: str):
    ldic = locals()
    exec('var = ' + open(trial_curated_file).read(), globals(), ldic)
    return ldic["var"]
