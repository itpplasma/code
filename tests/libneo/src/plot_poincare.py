# %%
import numpy as np
import matplotlib.pyplot as plt
n_periods = 100
n_fieldlines = 10
poincare_RZ = np.loadtxt('poincare.dat')
plt.figure()
for fieldline in range(n_fieldlines):
    plt.plot(poincare_RZ[fieldline*n_periods:(fieldline+1)*n_periods, 0],
             poincare_RZ[fieldline*n_periods:(fieldline+1)*n_periods, 1],
             '.', markersize=1)
plt.xlabel('R')
plt.ylabel('Z')
plt.axis('equal')
plt.show()