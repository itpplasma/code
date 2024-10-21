# %%
import numpy as np
import matplotlib.pyplot as plt
import glob

poincare_files = glob.glob('*.dat')
n_periods = 1000
n_fieldlines = 20
for poincare_file in poincare_files:
    print(poincare_file)
    poincare_RZ = np.loadtxt(poincare_file)
    plt.figure()
    for fieldline in range(n_fieldlines):
        plt.plot(poincare_RZ[fieldline*n_periods:(fieldline+1)*n_periods, 0],
                poincare_RZ[fieldline*n_periods:(fieldline+1)*n_periods, 1],
                '.', markersize=0.5)
    plt.xlabel('R')
    plt.ylabel('Z')
    plt.axis('equal')
plt.show()