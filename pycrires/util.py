import numpy as np
from scipy.signal import savgol_filter
from scipy.interpolate import interp1d
import os
from PyAstronomy.pyasl import fastRotBroad


def lowpass_filter(flux, window_length):
    """
    Function that low-pass filters a spectrum.

    Parameters
    ----------
    flux: np.ndarray
        Spectrum to low-pass filter
    window_length : int
        Length of the Savitsky-Golay filter to use
    Returns
    -------
    NoneType
        filtered: np.ndarray
        The low-pass filtered spectrum
    """
    nans = np.isnan(flux)
    if np.sum(nans) > 0.5*flux.size:
        return np.tile(np.nan, flux.size)
    else:
        window_length = min(window_length, 2*(np.sum(~nans)//2)-1)
        filtered = flux.copy()
        filtered[~nans] = np.array(savgol_filter(flux[~nans],
                                                 window_length, 2))
        return filtered


def highpass_filter(order_flux, window_length):
    """
    Function that high-pass filters a spectrum.

    Parameters
    ----------
    flux: np.ndarray
        Spectrum to high-pass filter
    window_length : int
        Length of the Savitsky-Golay filter to use
    Returns
    -------
    filtered: np.ndarray
        The high-pass filtered spectrum
    """
    continuum = lowpass_filter(order_flux, window_length)
    return order_flux - continuum


def mask_tellurics(order_flux, order_wl, lower_lim=0.5, upper_lim=2.0,
                   fill_val=np.nan):
    """
    Function that masks telluric absorption or emission lines.

    Parameters
    ----------
    order_flux: np.ndarray
        2D spectrum (N_rows, N_wavelengths) to apply the masking to.
    order_wl : np.ndarray
        2D array (N_rows, N_wavelengths) corresponding to the
        wavelength at each bin.
    lower_lim : float
        Lower limit for the masking. A value of 0.5 will mask all
        tellurics that have less than 50% flux w.r.t to the continuum
        envelope.
    upper_lim : float
        Upper limit for the masking. A value of 2. will mask all
        tellurics that have more than 100% excess flux w.r.t to the continuum
        envelope.
    fill_val :
        Value to fill the masked bins with.
    Returns
    -------
    masked_order: np.ndarray
        2D spectrum (N_rows, N_wavelengths) with applied masking.
    """
    wl = np.nanmedian(order_wl, axis=0)
    tot_spec = np.nansum(order_flux, axis=0)
    percentile = np.nanpercentile(tot_spec, 0.3)
    mask = tot_spec > percentile
    continuum = np.poly1d(np.polyfit(wl[mask], tot_spec[mask], 3))(wl)
    cont_normalized_flux = tot_spec/continuum
    to_mask = (cont_normalized_flux < lower_lim) + \
              (cont_normalized_flux > upper_lim)
    masked_order = np.copy(order_flux)
    masked_order[:, to_mask] = fill_val
    return masked_order


def fit_svd_kernel(order_flux, order_wl, star_model, max_shift=50, rcond=1e-3):
    """
    Function that tries to determine the line spread function of each row
    using a Singular Value Decomposition.

    Parameters
    ----------
     order_flux: np.ndarray
        2D spectrum (N_rows, N_wavelengths) to apply the masking to.
    order_wl : np.ndarray
        2D array (N_rows, N_wavelengths) corresponding to the 
        wavelength at each bin.
   star_model : np.ndarray
        2D array (N_rows, N_wavelengths) with the estimated stellar
        contribution to each row.
    max_shift : int
        Maximum allowed shift (in pixels) for the line spread function
        kernel.
    rcond : float
        Cutoff for small singular values in the inversion.
    
    Returns
    -------
    result: np.ndarray
        2D array (N_rows, N_wavelengths) with the stellar
        contribution to each row corrected for the local line spread
        function.
    """
    result = np.copy(order_flux)
    for i, (spec, wl, model) in enumerate(zip(order_flux,
                                              order_wl, star_model)):
        interpolator = interp1d(wl, model, bounds_error=False,
                                fill_value=np.nanmedian(model))
        shifts = np.arange(-max_shift, max_shift + 1e-10)
        dlam = np.nanmean(np.diff(wl))
        modes = [interpolator(wl + shift * dlam) for shift in shifts]
        modes = np.array(modes)
        modes[np.isnan(modes)] = 0
        proj_matrix = np.linalg.pinv(modes, rcond=rcond)
        spec[np.isnan(spec)] = 0
        amps = proj_matrix.T.dot(spec)
        reconstructed = amps.dot(modes)
        result[i] = reconstructed
    return result


def flag_outliers(order_flux, sigma=4, fill_value=np.nan):
    """
    Function that flags outliers in a 2D spectrum.

    Parameters
    ----------
    order_flux: np.ndarray
        2D spectrum (N_rows, N_wavelengths) to flag.
    sigma: float
        Values with a 'sigma' standard deviations from the mean will
        be flagged.
    fill_value: float
        Value to replace the outliers with
    
    Returns
    -------
    order_flux: np.ndarray
        2D spectrum (N_rows, N_wavelengths) with flagged values.
    """
    z = (order_flux - np.nanmedian(order_flux, axis=1)[:, np.newaxis])\
        / np.nanstd(order_flux, axis=1)[:, np.newaxis]
    outliers = z > sigma
    order_flux[outliers] = fill_value
    return order_flux


def load_BT_SETTL_template(temperature, log_g, vsini, wl_lims=[0.8, 3.]):
    """
    Function that loads a BT-SETTL template for the given
    temperature, surface gravity and vsin(i).

    Parameters
    ----------
    temperature: float
        Effective temperature of the model to load. The grid has a 
        resolution of 100 K, so will be rounded to the nearest value.
    log_g: flota
        Surface gravity of the model to load. The grid has a 
        resolution of 0.5 dex, so will be rounded to the nearest value.
    vsini : float
        Rotational velocity of the model to load used for the broadening
        kernel.
    wl_lim : list
        Lower and upper limits (in micron) of the wavelength range to load.
    
    Returns
    -------
    flux: np.ndarray
        Flux of the planetary template (in ergs/)
    """
    import wget
    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context
    #The file names contain the main parameters of the models:
    #lte{Teff/10}-{Logg}{[M/H]}a[alpha/H].GRIDNAME.7.spec.gz/bz2/xz
    t_val = int(np.round(temperature / 100))
    log_g_val = 0.5*np.round(log_g / 0.5)
    if t_val < 12:
        fname = 'lte{0:03d}-{1:.1f}-0.0a+0.0.BT-Settl.spec.7.bz2'.format(
            t_val, log_g_val)
    else:
        fname = 'lte{0:03d}.0-{1:.1f}-0.0a+0.0.BT-Settl.spec.7.xz'.format(
            t_val, log_g_val)

    data_path = os.path.join(os.getcwd(), 'bt_settl_data')
    if not os.path.exists(data_path):
        print("Making local data folder...")
        os.mkdir(data_path)
    fpath = os.path.join(data_path, fname)
    decompressed_fpath = fpath[:-4]
    if not os.path.exists(decompressed_fpath):
        if t_val < 12:
            url = 'https://phoenix.ens-lyon.fr/Grids/BT-Settl/CIFIST2011/SPECTRA/'+fname
        else:
            url = 'https://phoenix.ens-lyon.fr/Grids/BT-Settl/CIFIST2011_2015/SPECTRA/'+fname
        print('Downloading BT-SETTL spectra from:', url)
        file = wget.download(url, fpath)

        if t_val < 12:
            with open(fpath, 'rb') as file:
                import bz2
                data = file.read()
                decompressed_data = bz2.decompress(data)
        else:
            import lzma
            decompressed_data = lzma.open(fpath).read()
        with open(decompressed_fpath, 'wb') as file:
            file.write(decompressed_data)

    with open(decompressed_fpath, 'r') as file:
        data = file.readlines()
    wl = np.zeros(len(data))
    flux = np.zeros(len(data))

    for i,line in enumerate(data):
        split_line = line.split()
        approx_wl = float(split_line[0][:6])
        if (approx_wl > wl_lims[0]*1e4)*(approx_wl < wl_lims[1]*1e4):
            try:
                wl[i] = float(split_line[0])*1e-4
                flux[i] = 10**(float(split_line[1].replace('D','E'))-8)
            except:
                double_splitted = split_line[0].split('-')
                wl[i] = float(double_splitted[0])*1e-4
                if len(split_line) == 2:
                    flux[i] = 10**(float(double_splitted[1].replace(
                                'D', 'E')) - 8)
                else:
                    flux[i] = 10**(float((double_splitted[1]+'-' +
                                          double_splitted[2]).replace(
                                          'D', 'E')) - 8)
    if wl_lims is not None:
        mask = (wl > wl_lims[0])*(wl < wl_lims[1])
        wl, flux = wl[mask], flux[mask]
    sorting = np.argsort(wl)
    wl = wl[sorting]
    flux = flux[sorting]
    waves_even = np.linspace(np.min(wl), np.max(wl), wl.size*2)
    flux_even = interp1d(wl, flux)(waves_even)
    broad_flux = fastRotBroad(waves_even, flux_even, 0.5, vsini)
    return broad_flux, waves_even * 1000
