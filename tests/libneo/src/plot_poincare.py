# %%
import numpy as np
import matplotlib.pyplot as plt
n_periods = 400
n_fieldlines = 30
poincare_RZ = np.loadtxt('1.dat')
plt.figure()
for fieldline in range(n_fieldlines):
    plt.plot(poincare_RZ[fieldline*n_periods:(fieldline+1)*n_periods, 0],
             poincare_RZ[fieldline*n_periods:(fieldline+1)*n_periods, 1],
             '.', markersize=0.5)
plt.xlabel('R')
plt.ylabel('Z')
plt.axis('equal')
plt.show()