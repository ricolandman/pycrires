import numpy as np
from scipy.signal import savgol_filter
from scipy.interpolate import interp1d
import os
from PyAstronomy.pyasl import fastRotBroad

def lowpass_filter(flux, window_length):
    nans = np.isnan(flux)
    if np.sum(nans)>0.5*flux.size:
        return np.tile(np.nan, flux.size)
    else:
        window_length = min(window_length, 2*(np.sum(~nans)//2)-1)
        filtered = flux.copy()
        filtered[~nans] = np.array(savgol_filter(flux[~nans],
                        window_length, 2))
        return filtered

def highpass_filter(order_flux, window_length):
    continuum = lowpass_filter(order_flux, window_length)
    return order_flux - continuum

def mask_tellurics(order_flux, order_wl, lower_lim = 0.5, upper_lim=2.):
    wl = np.nanmedian(order_wl, axis=0)
    tot_spec = np.nansum(order_flux, axis=0)
    percentile = np.nanpercentile(tot_spec, 0.3)
    mask = tot_spec>percentile
    continuum = np.poly1d(np.polyfit(wl[mask], tot_spec[mask], 3))(wl)
    cont_normalized_flux = tot_spec/continuum
    to_mask = (cont_normalized_flux<lower_lim) + (cont_normalized_flux>upper_lim)
    masked_order = np.copy(order_flux)
    masked_order[:,to_mask] = np.nan
    return masked_order

def fit_svd_kernel(order_flux, order_wl, star_model, max_shift=50, rcond=1e-3):
    result = np.copy(order_flux)
    for i, (spec, wl, model) in enumerate(zip(order_flux, order_wl, star_model)):
        interpolator = interp1d(wl, model, bounds_error=False,
            fill_value=np.nanmedian(model))
        shifts = np.arange(-max_shift, max_shift+1e-10)
        dlam = np.nanmean(np.diff(wl))
        modes = [interpolator(wl+shift*dlam) for shift in shifts]
        modes = np.array(modes)
        modes[np.isnan(modes)] = 0
        proj_matrix = np.linalg.pinv(modes, rcond=rcond)
        spec[np.isnan(spec)] = 0
        amps = proj_matrix.T.dot(spec)
        reconstructed = amps.dot(modes)
        result[i] = reconstructed
    return result

def flag_outliers(order_spec, sigma=4):
    z = (order_spec - np.nanmedian(order_spec, axis=1)[:, np.newaxis])\
                /np.nanstd(order_spec, axis=1)[:, np.newaxis]
    outliers = z>sigma
    order_spec[outliers] = np.nan
    return order_spec


def load_BT_SETTL_template(temperature, log_g, vsini, wl_lims=[0.5, 3]):
    import wget
    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context
    #The file names contain the main parameters of the models:
    #lte{Teff/10}-{Logg}{[M/H]}a[alpha/H].GRIDNAME.7.spec.gz/bz2/xz
    t_val = int(np.round(temperature/100))
    log_g_val = 0.5*np.round(log_g/0.5)
    if t_val<12:
        fname = 'lte{0:03d}-{1:.1f}-0.0a+0.0.BT-Settl.spec.7.bz2'.format(t_val, log_g_val)
    else:
        fname = 'lte{0:03d}.0-{1:.1f}-0.0a+0.0.BT-Settl.spec.7.xz'.format(t_val, log_g_val)

    data_path = os.path.join(os.getcwd(), 'bt_settl_data')
    if not os.path.exists(data_path):
        print("Making local data folder...")
        os.mkdir(data_path)
    fpath = os.path.join(data_path, fname)
    decompressed_fpath = fpath[:-4]
    if not os.path.exists(decompressed_fpath):
        if t_val<12:
            url = 'https://phoenix.ens-lyon.fr/Grids/BT-Settl/CIFIST2011/SPECTRA/'+fname
        else:
            url = 'https://phoenix.ens-lyon.fr/Grids/BT-Settl/CIFIST2011_2015/SPECTRA/'+fname
        print('Downloading BT-SETTL spectra from:', url)
        file = wget.download(url,fpath)
    
        if t_val<12:
            with open(fpath, 'rb') as file:
                import bz2
                data = file.read()
                decompressed_data = bz2.decompress(data)
        else:
            import lzma
            decompressed_data = lzma.open(fpath).read()
        with open(decompressed_fpath,'wb') as file:
            file.write(decompressed_data)
    
    with open(decompressed_fpath, 'r') as file:
        data = file.readlines()
    wl = np.zeros(len(data))
    flux = np.zeros(len(data))

    for i,line in enumerate(data):
        split_line = line.split()
        approx_wl = float(split_line[0][:6])
        if (approx_wl>wl_lims[0]*1e4)*(approx_wl<wl_lims[1]*1e4):
            try:
                wl[i] = float(split_line[0])*1e-4
                flux[i] = 10**(float(split_line[1].replace('D','E'))-8)
            except:
                double_splitted = split_line[0].split('-')
                wl[i] = float(double_splitted[0])*1e-4
                if len(split_line)==2:
                    flux[i] = 10**(float(double_splitted[1].replace('D','E'))-8)
                else:
                    flux[i] = 10**(float((double_splitted[1]+'-'+double_splitted[2]).replace('D','E'))-8)
    if wl_lims is not None:
        mask = (wl>wl_lims[0])*(wl<wl_lims[1])
        wl, flux = wl[mask], flux[mask]
    sorting = np.argsort(wl)
    wl = wl[sorting]
    flux = flux[sorting]
    waves_even = np.linspace(np.min(wl), np.max(wl), wl.size*2)
    flux_even = interp1d(wl, flux)(waves_even)
    broad_flux = fastRotBroad(waves_even, flux_even, 0.5, vsini)
    return broad_flux, waves_even*1000