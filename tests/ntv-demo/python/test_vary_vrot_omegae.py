# %% Standard imports
import matplotlib.pyplot as plt
import os
import numpy as np

# Custom imports
from neo2_mars import generate_vrot_for_mars
from neo2_mars import generate_omega_e_for_mars
from neo2_mars import generate_nu_for_mars

# Import to test
from ntv_demo_parameter_study import read_profiles
from ntv_demo_parameter_study import calculate_omegae, read_omegae_and_vrot
from ntv_demo_parameter_study import get_omegae_linear_coefs

base_dir = "/proj/plasma/DATA/DEMO/MARS/BASE_INPUT_PROFILES"
negative_vrot_case = "/proj/plasma/DATA/DEMO/MARS/PROFWE/negative_vrot_case"
positive_vrot_case = "/proj/plasma/DATA/DEMO/MARS/PROFWE/positive_vrot_case"

def test_get_omegae_linear_coefs_visual_check():
    fontsize = 24
    profiles = read_profiles(base_dir)
    vrot = profiles["vrot"][1:,1]
    sqrtspol = profiles["vrot"][1:,0]
    offset, slope = get_omegae_linear_coefs(negative_vrot_case, positive_vrot_case)
    omegae_calculated = offset + slope * vrot
    fig, ax_left = plt.subplots(1, 1, figsize=(10, 8))
    ax_left.plot(sqrtspol, offset, '--k', label=r'offset $\delta$')
    ax_left.plot([],[], '--r', label=r'slope $\kappa$')
    ax_left.set_ylabel(r'$\delta$ [rad/s]', color='k', fontsize=fontsize)
    ax_left.tick_params(axis='y', labelcolor='k', color='k', labelsize=fontsize)
    ax_left.tick_params(axis='x', labelsize=fontsize)

    ax_right = ax_left.twinx()
    ax_right.plot(sqrtspol, slope, '--r')
    ax_right.set_ylabel(r'$\kappa$ [1]', color='r', fontsize=fontsize)
    ax_right.tick_params(axis='y', labelcolor='r', color='r', labelsize=fontsize)
    ax_right.spines['right'].set_color('r')

    ax_left.set_xlabel(r'$\rho_\mathrm{pol}$ [1]', fontsize=fontsize)
    ax_left.set_ylim([-4e3, 1e4])
    plt.show()

def test_calculate_omegae_visual_check():
    profiles = read_profiles(base_dir)
    vrot = profiles["vrot"][1:,1]
    sqrtspol = profiles["vrot"][1:,0]
    omegae = calculate_omegae(vrot/2)
    half_vrot_case = "/proj/plasma/DATA/DEMO/MARS/PROFWE/half_positive_vrot_case"
    omegae_negative_vrot, _ = read_omegae_and_vrot(negative_vrot_case)
    omegae_positive_vrot, _ = read_omegae_and_vrot(positive_vrot_case)
    omegae_half_vrot, _ = read_omegae_and_vrot(half_vrot_case)
    fig, ax = plt.subplots(1, 1, figsize=(10, 8))
    ax.plot(sqrtspol, omegae_negative_vrot, '-b', label='negative vrot')
    ax.plot(sqrtspol, omegae_positive_vrot, '-r', label='positive vrot')
    ax.plot(sqrtspol, omegae, '--g', label='calculated')
    ax.plot(sqrtspol, omegae_half_vrot, '.k', label='control')
    ax.set_ylabel(r'$\omega_{e}$ [rad/s]')
    ax.set_xlabel(r'$\rho_\mathrm{pol}$ [1]')
    ax.set_ylim([-1.2e4, 1e4])
    ax.set_xlim([0.01, 1])
    plt.legend()
    plt.show()

def test_profit_runs_visual_check():
    study = "/proj/plasma/DATA/DEMO/teams/parameter_study_profiles_MARS/vary_vrot_omegae_and_coilwidth/"
    base_run = "/proj/plasma/DATA/DEMO/MARS/BASE_INPUT_PROFILES"
    runs_color = (0.8, 0.8, 0.8)
    base_color = (0.6, 0.2, 0.2)
    fig, ax = plt.subplots(4, 2, figsize=(10, 8))
    ax_Te = ax[0, 0]
    ax_Ti = ax[0, 1]
    ax_n = ax[1, 0]
    ax_nue = ax[2, 0]
    ax_nui = ax[2, 1]
    ax_vrot = ax[3, 0]
    ax_omegae = ax[3, 1]
    number_of_ploted_runs = 0
    max_number_of_runs = 20
    for folder in os.listdir(study):
        if number_of_ploted_runs >= max_number_of_runs:
            break
        if folder.startswith('run_'):
            number_of_ploted_runs += 1
            profiles = read_profiles(os.path.join(study, folder))
            
            ax_Te.plot(profiles["Te"][1:,0], profiles["Te"][1:,1]/1000, color=runs_color)
            ax_Te.set_ylabel(r'$T_\mathrm{e}$ [keV]')
            ax_Te.set_xlabel(r'$\rho_\mathrm{pol}$ [1]')
            ax_Te.set_xlim([0.01, 1])
            
            ax_Ti.plot(profiles["Ti"][1:,0], profiles["Ti"][1:,1]/1000, color=runs_color)
            ax_Ti.set_ylabel(r'$T_\mathrm{i}$ [keV]')
            ax_Ti.set_xlabel(r'$\rho_\mathrm{pol}$ [1]')
            ax_Ti.set_xlim([0.01, 1])
            
            ax_n.plot(profiles["n"][1:,0], profiles["n"][1:,1], color=runs_color)
            ax_n.set_xlabel(r'$\rho_\mathrm{pol}$ [1]')
            ax_n.set_ylabel(r'$n$ [$\mathrm{m}^{-3}$]')
            ax_n.set_xlim([0.01, 1])

            ax_nue.plot(profiles["nue"][1:,0], profiles["nue"][1:,1], color=runs_color)
            ax_nue.set_xlabel(r'$\rho_\mathrm{pol}$ [1]')
            ax_nue.set_ylabel(r'$\nu_{e}$ [s$^{-1}$]')
            ax_nue.set_yscale('log')
            ax_nue.set_xlim([0.01, 1])

            ax_nui.plot(profiles["nui"][1:,0], profiles["nui"][1:,1], color=runs_color)
            ax_nui.set_xlabel(r'$\rho_\mathrm{pol}$ [1]')
            ax_nui.set_ylabel(r'$\nu_{i}$ [s$^{-1}$]')
            ax_nui.set_yscale('log')
            ax_nui.set_xlim([0.01, 1])

            ax_vrot.plot(profiles["vrot"][1:,0], profiles["vrot"][1:,1], color=runs_color)
            ax_vrot.set_xlabel(r'$\rho_\mathrm{pol}$ [1]')
            ax_vrot.set_ylabel(r'$V^\varphi$ [rad/s]')
            ax_vrot.set_ylim([-3e3, 1.6e4])
            ax_vrot.set_xlim([0.01, 1])

            ax_omegae.plot(profiles["omegae"][1:,0], profiles["omegae"][1:,1], color=runs_color)
            ax_omegae.set_xlabel(r'$\rho_\mathrm{pol}$ [1]')
            ax_omegae.set_ylabel(r'$\Omega_{e}$ [rad/s]')
            ax_omegae.set_ylim([-3e3, 1.6e4])
            ax_omegae.set_xlim([0.01, 1])

    profiles = read_profiles(base_dir)
    ax_Te.plot(profiles["Te"][1:,0], profiles["Te"][1:,1]/1000, color=base_color, label='base run')
    ax_Ti.plot(profiles["Ti"][1:,0], profiles["Ti"][1:,1]/1000, color=base_color, label='base run')
    ax_n.plot(profiles["n"][1:,0], profiles["n"][1:,1], color=base_color, label='base run')
    ax_nue.plot(profiles["nue"][1:,0], profiles["nue"][1:,1], color=base_color, label='base run')
    ax_nui.plot(profiles["nui"][1:,0], profiles["nui"][1:,1], color=base_color, label='base run')
    ax_vrot.plot(profiles["vrot"][1:,0], profiles["vrot"][1:,1], color=base_color, label='base run')
    ax_omegae.plot(profiles["omegae"][1:,0], profiles["omegae"][1:,1], color=base_color, label='base run')
    ax_Te.legend()
    ax_Ti.legend()
    ax_n.legend()
    ax_nue.legend()
    ax_nui.legend()
    ax_vrot.legend()
    ax_omegae.legend()
    fig.suptitle('run profiles', fontsize=16)
    plt.tight_layout()
    plt.show()

def test_read_profiles_visual_check():
    profiles = read_profiles(base_dir)
    fig, ax = plt.subplots(3, 2, figsize=(10, 8 ))
    ax[0, 0].plot(profiles["Te"][1:,0], profiles["Te"][1:,1]/1000)
    ax[0, 0].set_ylabel(r'$T_\mathrm{e}$ [keV]')
    ax[0, 0].set_xlabel(r'$\rho_\mathrm{pol}$ [1]')
    ax[0, 1].plot(profiles["Ti"][1:,0], profiles["Ti"][1:,1]/1000)
    ax[0, 1].set_ylabel(r'$T_\mathrm{i}$ [keV]')
    ax[0, 1].set_xlabel(r'$\rho_\mathrm{pol}$ [1]')
    ax[1, 0].plot(profiles["n"][1:,0], profiles["n"][1:,1])
    ax[1, 0].set_ylabel(r'$n$ [$\mathrm{m}^{-3}$]')
    ax[1, 0].set_xlabel(r'$\rho_\mathrm{pol}$ [1]')
    ax[1, 1].plot(profiles["vrot"][1:,0], profiles["vrot"][1:,1])
    ax[1, 1].set_ylabel(r'$v_\mathrm{rot}$ [rad/s]')
    ax[1, 1].set_xlabel(r'$\rho_\mathrm{pol}$ [1]')
    ax[2, 0].plot(profiles["nue"][1:,0], profiles["nue"][1:,1])
    ax[2, 0].set_ylabel(r'$\nu_{e}$ [s$^{-1}$]')
    ax[2, 0].set_xlabel(r'$\rho_\mathrm{pol}$ [1]')
    ax[2, 0].set_yscale('log')
    ax[2, 1].plot(profiles["nui"][1:,0], profiles["nui"][1:,1])
    ax[2, 1].set_ylabel(r'$\nu_{i}$ [s$^{-1}$]')
    ax[2, 1].set_xlabel(r'$\rho_\mathrm{pol}$ [1]')
    ax[2, 1].set_yscale('log')
    plt.show()

if __name__ == "__main__":
    test_calculate_omegae_visual_check()    
    test_profit_runs_visual_check()
    test_get_omegae_linear_coefs_visual_check()
    test_read_profiles_visual_check()