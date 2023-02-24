import numpy as np
import matplotlib.pyplot as plt

import polyplotter as pp

spec_fname = "2023-02-06-15-49-43_zoomed_NoneZ.npy"

data = pp.load_spec(spec_fname)

freqs = data[0]
s11 = data[1]

pp.plot_s11(freqs, s11, fit=True)
plt.show()
