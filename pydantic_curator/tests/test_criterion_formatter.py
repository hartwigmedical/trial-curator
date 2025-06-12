

def test_format():
    formatted = '''
    not {
       or {
          histology(histology_type="sarcomatoid"),
          histology(histology_type="spindle cell"),
          histology(histology_type="neuroendocrine small cell"),
          and {
            not {
                treatmentoption(treatment_option="standard of care")
            },
            priortherapy(therapy="taxane regimens", number_of_prior_lines(min_inclusive=1))
          }
       }
    }
    '''

def test_timing_format():
    formatted = '''
    timing(reference="now", ) {
       or {
          histology(histology_type="sarcomatoid"),
          histology(histology_type="spindle cell"),
          histology(histology_type="neuroendocrine small cell"),
          and {
            not {
                treatmentoption(treatment_option="standard of care")
            },
            priortherapy(therapy="taxane regimens", number_of_prior_lines(min_inclusive=1))
          }
       }
    }
    '''
