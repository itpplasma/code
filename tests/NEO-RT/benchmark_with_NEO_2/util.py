import f90nml
import numpy as np

qe = 4.803204e-10
me = 9.109382e-28
mu = 1.660538e-24
c = 2.997925e10
kb = 1.381649e-16
eV = 1.602176e-12

def read_variables(file_path):
    # Dictionary to store variables
    variables = {}

    # Open the file
    with open(file_path, 'r') as file:
        # Read lines from the file
        lines = file.readlines()

        # Iterate over each line
        for line in lines:
            # Split the line by whitespace
            parts = line.split()

            # Check if the line contains variable information
            if len(parts) >= 3 and parts[2] == '=':
                # Extract variable name and value
                variable_name = parts[1]
                variable_value = ' '.join(parts[3:])

                # Store variable in the dictionary
                try:
                    variables[variable_name] = float(variable_value)
                except ValueError:
                    variables[variable_name] = variable_value

    return variables

def load_transport(prefix, neo2_output):
    """
    Loads transport coefficients of NEO-RT and NEO-2 in a combined manner
    """
    input = f90nml.read(f'{prefix}.in')
    data = np.loadtxt(f'{prefix}.out')
    vars_neort = read_variables(f'{prefix}_magfie_param.out')

    R0 = float(vars_neort['R0'])
    a = float(vars_neort['a'])
    iota = float(vars_neort['iota'])
    B0 = float(vars_neort['B0'])
    s = float(vars_neort['s'])
    vth = float(vars_neort['vth'])

    qi = input['params']['qs']*qe
    mi = input['params']['ms']*mu

    Dp = np.pi*vth**3/(16.0*R0*iota*(qi*B0/(mi*c))**2)
    dsdreff_neort = 2.0/a*np.sqrt(s)

    dsdreff_neo2_interp = get_neo2_interp(neo2_output, 'avnabpsi')
    dsdreff_neo2 = dsdreff_neo2_interp(s)

    assert(same_order_of_magnitude(dsdreff_neo2, dsdreff_neort))

    coeffs_neort = get_transport_coeffs(data, Dp, dsdreff_neort, dsdreff_neo2)
    for k in coeffs_neort.keys():
        vars_neort[k] = coeffs_neort[k]

    return vars_neort

def same_order_of_magnitude(x, y):
    return (np.log10(np.abs(1.0 - x/y)) < 0.0)


def get_transport_coeffs(data_neort, Dp, dsdreff_neort, dsdreff_neo2):
    data = data_neort.copy()
    data[1:] = data[1:]*Dp*dsdreff_neort**2/dsdreff_neo2**2
    ret = {}

    ret['M_t'] = data[0]
    ret['D11_pco'] = data[1]
    ret['D11_pct'] = data[2]
    ret['D11_t'] = data[3]
    ret['D11'] = data[4]
    ret['D12_pco'] = data[5]
    ret['D12_pct'] = data[6]
    ret['D12_t'] = data[7]
    ret['D12'] = data[8]

    return ret


def load_torque(prefix, neo2_output):
    """
    Loads torque of NEO-RT and NEO-2 in a combined manner
    """

    from get_torque import get_torque

    data = np.loadtxt(f'{prefix}_torque.out')
    vars_neort = read_variables(f'{prefix}_magfie_param.out')
    Tphi_neort = get_torque(data)

    vars_neort ['Tphi'] = Tphi_neort

    return vars_neort

def get_neo2_interp(neo2_output, key):
    from scipy.interpolate import interp1d
    return interp1d(neo2_output['boozer_s'][:], neo2_output[key][:])

def fill_template(template, vars):
    text = template
    for key, var in vars.items():
        text = text.replace('{'+str(key)+'}', str(var))
    return text
