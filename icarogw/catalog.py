from .jax_pal import *
from .conversions import radec2indeces, indices2radec, M2m, m2M
from .cosmology import galaxy_MF, kcorr, log_powerlaw_absM_rate

import healpy as hp
import h5py
import matplotlib.pyplot as plt
from tqdm import tqdm

LOWERL=onp.nan_to_num(-onp.inf)

def user_normal(x,mu,sigma):
    ''' 
    A utility function meant only for this module. It returns a normalized gaussian distribution
    
    Parameters
    ----------
    x, mu, sigma: jnp.arrays
        Points at which to evaluate the gaussian with mean mu and std sigma
    
    Returns
    -------
    Values
    
    '''
    return jnp.power(2*jnp.pi*(sigma**2),-0.5)*jnp.exp(-0.5*jnp.power((x-mu)/sigma,2.))

def EM_likelihood_prior_differential_volume(z,zobs,sigmaz,cosmology,Numsigma=1.,ptype='uniform'):
    ''' 
    A utility function meant only for this module. Calculates the EM likelihood in redshift times a uniform in comoving volume prior
    
    Parameters
    ----------
    z: jnp.array
        Values at which to evaluate the EM likelihood times the prior. This is usually and array that starts from 0 and goes to zcut
    zobs: float 
        Central value of the galaxy redshift
    sigmaobs: float
        Std of galaxy redshift localization. Note if flat EM likelihood sigma is the half-widht of the box distribution.
    cosmology: Class
        Cosmology class from icarogw
    Numsigma: float
        Half Width for the uniform distribution method in terms of sigmaz
    ptype: string
        Type of EM likelihood, ''uniform'' for uniform distribution, ''gaussian'' for gaussian
    
    Returns
    -------
    Values of the EM likelihood times the prior evaluated on z
    
    '''
    
    # Lower limit for the integration. A galaxy must be at a positive redshift
    zvalmin=jnp.array([1e-6,zobs-Numsigma*sigmaz]).max()
    #zvalmax=jnp.array([z.max(),zobs+Numsigma*sigmaz]).min()    
    
    if ptype=='uniform':
        
        # higher limit for the integration. If it is localized  partialy above z_cut, it counts less
        zvalmax=zobs+Numsigma*sigmaz
        if zvalmax<=zvalmin:
            return jnp.zeros_like(z)
    
        prior_eval=4*jnp.pi*cosmology.dVc_by_dzdOmega_at_z(z)*((z>=(zobs-Numsigma*sigmaz)) & (z<=(zobs+Numsigma*sigmaz)))/(cosmology.z2Vc(zvalmax)-cosmology.z2Vc(zvalmin))
    elif ptype=='gaussian':
        
        zvalmax=zobs+5.*sigmaz
        if zvalmax<=zvalmin:
            return jnp.zeros_like(z)
    
        prior_eval=cosmology.dVc_by_dzdOmega_at_z(z)*user_normal(z,zobs,sigmaz)
        zproxy=jnp.linspace(zvalmin,zvalmax,5000)
        normfact=trapz(cosmology.dVc_by_dzdOmega_at_z(zproxy)*user_normal(zproxy,zobs,sigmaz),zproxy)
        
        if normfact==0.:
            print(zobs,sigmaz)
            raise ValueError('Normalization failed')
            
        if onp.isnan(normfact):
            print(zobs,sigmaz)
            raise ValueError('Normalization failed')
            
        prior_eval/=normfact
        
    elif ptype=='gaussian_nocom':
        
        zvalmax=zobs+5.*sigmaz
        if zvalmax<=zvalmin:
            return jnp.zeros_like(z)
    
        prior_eval=user_normal(z,zobs,sigmaz)
        zproxy=jnp.linspace(zvalmin,zvalmax,5000)
        normfact=trapz(user_normal(zproxy,zobs,sigmaz),zproxy)
        
        if normfact==0.:
            print(zobs,sigmaz)
            raise ValueError('Normalization failed')
            
        if onp.isnan(normfact):
            print(zobs,sigmaz)
            raise ValueError('Normalization failed')
            
        prior_eval/=normfact

    return prior_eval

    
def generate_fake_catalog(zmin,zmax,sigmaz,maglim,band,cosmology,outname='fake_cat.hdf5'):
    '''
    Generates a fake catalog
    
    Parameters
    ----------
    zmin,zmax,maglim: floats
        Minimum, maximum of redshift and magnitude limit for the galaxy catalog
    band: string
        name of the band, use W1, K or bJ
    cosmology: cosmology class
        The cosmology class from icarogwCAT
    outname: string
        Name of the output file, must be an hdf5 file.
    '''
    
    MF_gal=galaxy_MF(band=band)
    MF_gal.build_MF(cosmology)
    Mcheck=jnp.linspace(MF_gal.Mminobs,MF_gal.Mmaxobs,1000)
    dM=Mcheck[1]-Mcheck[0]
    Numdensity=trapz(MF_gal.evaluate(Mcheck),Mcheck)
    
    kcorr_gal=kcorr(band)
    zarr=onp.linspace(zmin,zmax,10000)
    dz=zarr[1]-zarr[0]
    output_dict = {'ra':[],'dec':[],'z':[],'sigmaz':[],'m_'+band:[]}
    for zz in tqdm(zarr):
        dVc=(cosmology.dVc_by_dzdOmega_at_z(jnp.array([zz+dz]))+cosmology.dVc_by_dzdOmega_at_z(jnp.array([zz])))*0.5*dz*jnp.pi*4.  
        Numgal=Numdensity*dVc        
        Mvals=MF_gal.sample(int(jnp2onp(Numgal))) 
        zvals=onp.ones_like(Mvals)*zz
        mvals=M2m(Mvals,cosmology.z2dl(zvals),kcorr_gal(zvals))
        to_save=onp.where(mvals<=maglim)[0]
        output_dict['ra'].append(onp.random.uniform(0,2*onp.pi,size=len(to_save)))
        output_dict['dec'].append(onp.arccos(onp.random.uniform(-1.,1.,size=len(to_save)))-onp.pi/2.)
        output_dict['z'].append(zvals[to_save])
        output_dict['sigmaz'].append(onp.ones_like(to_save)*sigmaz)
        output_dict['m_'+band].append(mvals[to_save])
    
    for key in output_dict.keys():
        output_dict[key]=jnp2onp(onp.hstack(output_dict[key]))
        
    hf = h5py.File(outname, 'w')
    for key in output_dict.keys():
        hf.create_dataset(key, data=output_dict[key])
    hf.close()
    return output_dict

class galaxy_catalog(object):
    '''
    A class to handle galaxy catalogs. This class creates a hdf5 file containing all the necessary informations.
    '''
    
    
    def __init__(self):
        pass
    
    def create_hdf5(self,filename,cat_data,band,nside):
        '''
        Creates the HDF5 file

        Parameters
        ----------
        filename: string
            HDF5 file name to create
        cat_data: dictionary
            Dictionary of arrays containings for each galaxy 'ra': right ascensions in rad, 'dec': declination in radians
            'z': galaxy redshift, 'sigmaz': redshift uncertainty (can not be zero), 'm': apparent magnitude.
        band: string
            Band to use for the background corrections, need to be compatible with apparent magnitude. Bands available
            'K', 'W1', 'bJ'
        nside: int
            Nside to use for the healpy pixelization
        '''
        
        # This for loop removes the galaxies with NaNs or inf as entries 
        for key in list(cat_data.keys()):
            tokeep=onp.where(onp.isfinite(cat_data[key]))[0]
            cat_data={subkey:cat_data[subkey][tokeep] for subkey in list(cat_data.keys())}
        
        # Pixelize the galaxies
        cat_data['sky_indices'] = radec2indeces(cat_data['ra'],cat_data['dec'],nside)
        
        with h5py.File(filename,'w-') as f:

            cat=f.create_group('catalog')
            cat.attrs['band']=band
            cat.attrs['nside']=nside
            cat.attrs['npixels']=hp.nside2npix(nside)
            cat.attrs['dOmega_sterad']=hp.nside2pixarea(nside,degrees=False)
            cat.attrs['dOmega_deg2']=hp.nside2pixarea(nside,degrees=True)
            
            for vv in ['ra','dec','z','sigmaz','m','sky_indices']:            
                cat.create_dataset(vv,data=cat_data[vv])
                
            cat.attrs['Ngal']=len(cat['z'])  
            
        self.hdf5pointer = h5py.File(filename,'r+')
        self.calc_kcorr=kcorr(self.hdf5pointer['catalog'].attrs['band'])
    
    def load_hdf5(self,filename,cosmo_ref=None,epsilon=None):
        '''
        Loads the catalog HDF5 file
        
        Parameters
        ----------
        filename: string
            Name of the catalgo HDF5 file to load.
        cosmo_ref: class 
            Cosmology class used to create the catalog
        epsilon: float
            Luminosity weight index used to create the galaxy density interpolant
        '''
        
        self.hdf5pointer = h5py.File(filename,'r')
        self.sch_fun=galaxy_MF(band=self.hdf5pointer['catalog'].attrs['band'])
        self.calc_kcorr=kcorr(self.hdf5pointer['catalog'].attrs['band'])
        # Stores it internally
        try:
            if self.hdf5pointer['catalog/mthr_map'].attrs['mthr_percentile'] == 'empty':
                self.mthr_map = 'empty'
            else:
                self.mthr_map = onp2jnp(self.hdf5pointer['catalog/mthr_map/mthr_sky'][:])
            print('Loading apparent magnitude threshold map')
        except:
            print('Apparent magnitude threshold not present')

        if cosmo_ref is not None:
            self.sch_fun.build_MF(cosmo_ref)
        
        if epsilon is not None:
            self.sch_fun.build_effective_number_density_interpolant(epsilon)

        if cosmo_ref is None:
            raise ValueError('You need to provide a cosmology if you want to load the interpolant')
        
        self.sch_fun.build_effective_number_density_interpolant(
            self.hdf5pointer['catalog/dNgal_dzdOm_interpolant'].attrs['epsilon'])
        interpogroup = self.hdf5pointer['catalog/dNgal_dzdOm_interpolant']
        
        self.dNgal_dzdOm_vals = []
        for i in range(self.hdf5pointer['catalog'].attrs['npixels']):
            self.dNgal_dzdOm_vals.append(interpogroup['vals_pixel_{:d}'.format(i)][:])
        self.dNgal_dzdOm_vals = onp.column_stack(self.dNgal_dzdOm_vals)
        self.dNgal_dzdOm_vals = onp2jnp(self.dNgal_dzdOm_vals)

        self.dNgal_dzdOm_vals = jnp.exp(self.dNgal_dzdOm_vals.astype(float))

        self.dNgal_dzdOm_sky_mean = jnp.mean(self.dNgal_dzdOm_vals,axis=1)
        self.z_grid = onp2jnp(interpogroup['z_grid'][:])
        self.pixel_grid = jnp.arange(0,self.hdf5pointer['catalog'].attrs['npixels'],1).astype(int)

        
        try:
            if cosmo_ref is None:
                raise ValueError('You need to provide a cosmology if you want to load the interpolant')
            
            self.sch_fun.build_effective_number_density_interpolant(
                self.hdf5pointer['catalog/dNgal_dzdOm_interpolant'].attrs['epsilon'])
            interpogroup = self.hdf5pointer['catalog/dNgal_dzdOm_interpolant']
            
            self.dNgal_dzdOm_vals = []
            for i in range(self.hdf5pointer['catalog'].attrs['npixels']):
                self.dNgal_dzdOm_vals.append(interpogroup['vals_pixel_{:d}'.format(i)][:])
            self.dNgal_dzdOm_vals = onp.column_stack(self.dNgal_dzdOm_vals)
            self.dNgal_dzdOm_vals = onp2jnp(self.dNgal_dzdOm_vals)

            self.dNgal_dzdOm_vals = self.dNgal_dzdOm_vals.at[jnp.isnan(self.dNgal_dzdOm_vals)].set(-jnp.inf)
            self.dNgal_dzdOm_vals = jnp.exp(self.dNgal_dzdOm_vals.astype(float))

            self.dNgal_dzdOm_sky_mean = jnp.mean(self.dNgal_dzdOm_vals,axis=1)
            self.z_grid = onp2jnp(interpogroup['z_grid'][:])
            self.pixel_grid = jnp.arange(0,self.hdf5pointer['catalog'].attrs['npixels'],1).astype(int)
            
            print('Loading Galaxy density interpolant')
            
            
        except:
            print('interpolant not loaded')
    
    def calculate_mthr(self,mthr_percentile=50,nside_mthr=None):
        '''
        Calculates the apparent magnitude threshold as a function of the sky pixel.
        The apparent magnitude threshold is defined from the inverse CDF of galaxies reported in each pixel.
        
        Parameters
        ----------
        mthr_percentile: float
            Percentile to use to calculate the apparent magnitude threshold
        nside_mthr: int 
            Nside to compute threshold, it should be higher or equal than the one used to pixelise galaxies.
        '''
        
        if nside_mthr is None:
            nside_mthr = int(self.hdf5pointer['catalog'].attrs['nside'])
        skypixmthr = radec2indeces(self.hdf5pointer['catalog/ra'][:],self.hdf5pointer['catalog/dec'][:],nside_mthr)
        npixelsmthr = hp.nside2npix(nside_mthr)
        
        try:
            mthgroup = self.hdf5pointer['catalog'].create_group('mthr_map')
            mthgroup.attrs['mthr_percentile'] = mthr_percentile
            mthgroup.attrs['nside_mthr'] = nside_mthr
            mthgroup.create_dataset('mthr_sky',data=onp.nan_to_num(
                -onp.ones(self.hdf5pointer['catalog'].attrs['npixels'])*onp.inf))
            indx_sky = 0 # An arra
        except:
            mthgroup = self.hdf5pointer['catalog/mthr_map']
            indx_sky = mthgroup.attrs['sky_checkpoint']
            print('Group already exists, resuming from pixel {:d}'.format(indx_sky))
        
        if mthr_percentile !='empty':
            # The block below computes the apparent magnitude threshold
            skyloop=onp.arange(indx_sky,self.hdf5pointer['catalog'].attrs['npixels'],1).astype(int)

            for indx in tqdm(skyloop,desc='Calculating mthr in pixels'):
                mthgroup.attrs['sky_checkpoint']=indx
                rap, decp = indices2radec(indx,self.hdf5pointer['catalog'].attrs['nside'])
                bigpix = radec2indeces(rap,decp,nside_mthr)               
                ind=onp.where(skypixmthr==bigpix)[0]
                if ind.size==0:
                    continue
                mthgroup['mthr_sky'][indx] = onp.percentile(self.hdf5pointer['catalog/m'][ind],
                                                           mthgroup.attrs['mthr_percentile'])


            # The block below throws away all the galaxies fainter than
            # the apparent magnitude threshold
            castmthr=mthgroup['mthr_sky'][:][self.hdf5pointer['catalog/sky_indices'][:]]
            tokeep=onp.where(self.hdf5pointer['catalog/m'][:]<=castmthr)[0]
            for vv in ['ra','dec','z','sigmaz','m','sky_indices']:
                tosave=self.hdf5pointer['catalog'][vv][:][tokeep]
                del self.hdf5pointer['catalog'][vv]       
                self.hdf5pointer['catalog'].create_dataset(vv,data=tosave)

            self.hdf5pointer['catalog'].attrs['Ngal']=len(tokeep)
            # Stores it internally
            self.mthr_map = onp2jnp(self.hdf5pointer['catalog/mthr_map/mthr_sky'][:])
        else:
            # Set the counter to the last loop point
            mthgroup.attrs['sky_checkpoint']=self.hdf5pointer['catalog'].attrs['npixels']-1
            self.mthr_map='empty'
    
    def return_counts_map(self):
        '''
        Returns the galaxy counts in the skymap as onp.array
        '''
        npixels = self.hdf5pointer['catalog'].attrs['Ngal']
        counts_map = onp.zeros(npixels)
        for indx in range(npixels):
            ind=onp.where(self.hdf5pointer['catalog/sky_indices'][:]==indx)[0]
            counts_map[indx]=len(ind)
            if ind.size==0:
                continue
        return counts_map
                
    def plot_mthr_map(self,**kwargs):
        '''
        Plots the mthr_map. Use **kwargs parameters for the hp.mollview
        '''
        mtr_map = self.hdf5pointer['catalog/mthr_map/mthr_sky'][:]
        mtr_map[mtr_map==LOWERL]=hp.UNSEEN
        mtr_map=hp.ma(mtr_map)
        hp.mollview(mtr_map,**kwargs)

    def plot_counts_map(self,**kwargs):
        '''
        Plots galaxy counts map. Use **kwargs parameters for the hp.mollview
        '''
        count=self.return_counts_map()
        count[count==0]=hp.UNSEEN
        count=hp.ma(count)
        hp.mollview(count,**kwargs)
        
    def calc_Mthr(self,z,radec_indices,cosmology,dl=None):
        ''' 
        This function returns the Absolute magnitude threshold calculated from the apparent magnitude threshold
        
        Parameters
        ----------
        z: jnp.array
            Redshift
        radec_indices: jnp.array
            Healpy indices
        cosmology: class 
            cosmology class
        dl: jnp.array
            dl values already calculated
        
        Returns
        -------
        Mthr: jnp.array
            Apparent magnitude threshold
        '''
        
        if dl is None:
            dl=cosmology.z2dl(z)
        
        mthr_arrays=self.mthr_map[radec_indices]
        return m2M(mthr_arrays,dl,self.calc_kcorr(z))
    
        
    def calc_dN_by_dzdOmega_interpolant(self,cosmo_ref,epsilon,
                                        Nintegration=10,Numsigma=1,
                                        zcut=None,ptype='uniform'):
        '''
        Fits the dNgal/dzdOmega interpolant
        
        Parameters
        ----------
        cosmo_ref: class 
            Cosmology used to compute the differential of comoving volume (normalized)
        epsilon: float
            Luminosity weight
        Nres: int 
            Increasing factor for the interpolation array in z
        Numsigma: float
            Half Width for the uniform distribution method in terms of sigmaz
        zcut: float
            Redshift where to cut the galaxy catalog, after zcut the completeness is 0
        ptype: string
            'uniform' or 'gaussian' for the EM likelihood type of galaxies
        '''
        
        self.sch_fun=galaxy_MF(band=self.hdf5pointer['catalog'].attrs['band'])
        self.sch_fun.build_effective_number_density_interpolant(epsilon)
        
        # Overrides the num of sigma for the gaussian
        if (ptype == 'gaussian') | (ptype == 'gaussian_nocom'):
            print('Setting 5 sigma for the gaussian normalization')
            Numsigma=5.
            
        try:
            interpogroup = self.hdf5pointer['catalog'].create_group('dNgal_dzdOm_interpolant')
            interpogroup.attrs['epsilon'] = epsilon
            interpogroup.attrs['ptype']=ptype
            interpogroup.attrs['Nintegration']=Nintegration
            interpogroup.attrs['Numsigma']=Numsigma
            interpogroup.attrs['zcut']=zcut
            indx_sky = 0 # An arra
        except:
            interpogroup = self.hdf5pointer['catalog/dNgal_dzdOm_interpolant']
            indx_sky = interpogroup.attrs['sky_checkpoint']
            print('Group already exists, resuming from pixel {:d}'.format(indx_sky))
        
        self.sch_fun.build_MF(cosmo_ref)
        
        cat_data=self.hdf5pointer['catalog']
        
        # If zcut is none, it uses the maximum of the cosmology
        if zcut is None:
            zcut = cosmo_ref.zmax
        
        # Selects all the galaxies that have support below zcut and above 1e-6
        idx_in_range = onp.where((cat_data['z'][:]-Numsigma*cat_data['sigmaz'][:]<=zcut) & (cat_data['z'][:]+Numsigma*cat_data['sigmaz'][:]>=1e-6))[0]
        if len(idx_in_range)==0:
            raise ValueError('There are no galaxies in the redshift range 1e-6 - {:f}'.format(maxz))
                
        interpolation_width = onp.empty(len(idx_in_range),dtype=onp.float32)
        j = 0
        for i in tqdm(idx_in_range,desc='Looping on galaxies to find width'):
            zmin = onp.max([cat_data['z'][i]-Numsigma*cat_data['sigmaz'][i],1e-6])
            zmax = onp.min([cat_data['z'][i]+Numsigma*cat_data['sigmaz'][i],zcut])
            if zmax>=cosmo_ref.zmax:
                print(minz,maxz)
                raise ValueError('The maximum redshift for interpolation is too high w.r.t the cosmology class')        
            interpolation_width[j]=zmax-zmin
            j+=1
            
        idx_sorted = onp.argsort(interpolation_width)
        del interpolation_width
        idx_sorted = idx_sorted[::-1] # Decreasing order
        
        z_grid = onp.linspace(1e-6,zcut,Nintegration)
        # Note that idx_in_range[idx_sorted] is the label of galaxies such that the 
        # interpolation width is sorted in decreasing order
        for i in tqdm(idx_in_range[idx_sorted],desc='Looping galaxies to find array'):
            zmin = onp.max([cat_data['z'][i]-Numsigma*cat_data['sigmaz'][i],1e-6])
            zmax = onp.min([cat_data['z'][i]+Numsigma*cat_data['sigmaz'][i],zcut])
            zinterpolator = onp.linspace(zmin,zmax,Nintegration)
            delta=(zmax-zmin)/Nintegration
            z_grid = onp.sort(onp.hstack([z_grid,zinterpolator]))
            even_in = onp.arange(0,len(z_grid),2)
            odd_in = onp.arange(1,len(z_grid),2)
            z_even = z_grid[even_in]
            diffv = onp.diff(z_even)
            to_eliminate = odd_in[onp.where(diffv<delta)[0]]
            z_grid = onp.delete(z_grid,to_eliminate)
            
        z_grid = onp.unique(z_grid)
             
        absM_rate=log_powerlaw_absM_rate(epsilon=epsilon)
        print('Z array is long {:d}'.format(len(z_grid)))

        if indx_sky == 0:
            interpogroup.create_dataset('z_grid', data = z_grid)
            interpogroup.create_dataset('pixel_grid', data = onp.arange(0,self.hdf5pointer['catalog'].attrs['npixels'],1).astype(int))
        
        skyloop=onp.arange(indx_sky,self.hdf5pointer['catalog'].attrs['npixels'],1).astype(int)
        cpind=cat_data['sky_indices'][:][idx_in_range]    
        
        for i in tqdm(skyloop,desc='Calculating interpolant'):
            interpogroup.attrs['sky_checkpoint']=i
            gal_index=jnp.where(cpind==i)[0]
            if len(gal_index)==0:
                tos = onp.zeros_like(z_grid)
                tos[:] = onp.nan
                interpogroup.create_dataset('vals_pixel_{:d}'.format(i), data = tos,dtype = onp.float16)
                del tos
                continue
            
            interpo = 0.
            
            for gal in gal_index:
                # List of galaxy catalog density in increasing order per pixel. This corresponds to Eq. 2.35 on the overleaf document
                Mv=m2M(cat_data['m'][idx_in_range[gal]],cosmo_ref.z2dl(z_grid),self.calc_kcorr(z_grid))               
                interpo+=absM_rate.evaluate(self.sch_fun,Mv)*EM_likelihood_prior_differential_volume(z_grid,
                                                            cat_data['z'][idx_in_range[gal]],cat_data['sigmaz'][idx_in_range[gal]],cosmo_ref
                                                            ,Numsigma=Numsigma,ptype=ptype)/self.hdf5pointer['catalog'].attrs['dOmega_sterad']
            
            interpo[interpo==0.]=onp.nan                
            interpo = onp.float16(onp.log(interpo))
            interpogroup.create_dataset('vals_pixel_{:d}'.format(i), data = interpo, dtype = onp.float16)
        
        self.hdf5pointer.close()
                
    def effective_galaxy_number_interpolant(self,z,skypos,cosmology,dl=None,average=False):
        '''
        Returns an evaluation of dNgal/dzdOmega, it requires `calc_dN_by_dzdOmega_interpolant` to be called first.
        
        Parameters
        ----------
        z: jnp.array
            Redshift array
        skypos: jnp.array
            Array containing the healpix indeces where to evaluate the interpolant
        cosmology: class
            cosmology class to use for the computation
        dl: jnp.array
            Luminosity distance in Mpc
        average: bool
            Use the sky averaged differential of effective number of galaxies in each pixel
        '''
       
        originshape=z.shape
        z=z.flatten()
        self.sch_fun.build_MF(cosmology)
        skypos=skypos.flatten()
        
        if dl is None:
            dl=cosmology.z2dl(z)
        dl=dl.flatten()
        
        if isinstance(self.mthr_map, str):
            return jnp.zeros(len(z)).reshape(originshape), (self.sch_fun.background_effective_galaxy_density(-jnp.inf*jnp.ones_like(z))*cosmology.dVc_by_dzdOmega_at_z(z)).reshape(originshape)
        
        Mthr_array=self.calc_Mthr(z,skypos,cosmology,dl=dl)
        # Baiscally tells that if you are above the maximum interpolation range, you detect nothing
        Mthr_array=Mthr_array.at[z>self.z_grid[-1]].set(-jnp.inf)
        gcpart,bgpart=jnp.zeros_like(z),jnp.zeros_like(z)
        
        if average:
            gcpart=jnp.interp(z,self.z_grid,self.dNgal_dzdOm_sky_mean,left=0.,right=0.)
        else:        
            gcpart=interpn((self.z_grid,self.pixel_grid),self.dNgal_dzdOm_vals,jnp.column_stack([z,skypos]),bounds_error=False,
                                fill_value=0.,method='linear') # If a posterior samples fall outside, then you return 0
        
        bgpart=self.sch_fun.background_effective_galaxy_density(Mthr_array)*cosmology.dVc_by_dzdOmega_at_z(z)
        
        return gcpart.reshape(originshape),bgpart.reshape(originshape)
        
    def check_differential_effective_galaxies(self,z,radec_indices_list,cosmology):
        '''
        This method checks the comoving volume distribution built from the catalog. It is basically a complementary check to the galaxy schecther function
        distribution
        
        Parameters
        ----------
        z: jnp.array
            Array of redshifts where to evaluate the effective galaxy density
        radec_indices: jnp.array
            Array of pixels on which you wish to average the galaxy density
        cosmology: class
            cosmology class to use
            
        Returns
        -------
        gcp: jnp.array
            Effective galaxy density from the catalog
        bgp: jnp.array
            Effective galaxy density from the background correction
        inco: jnp.array
            Incompliteness array
        fig: object
            Handle to the figure object
        ax: object
            Handle to the axis object
        '''
        
        gcp,bgp,inco=jnp.zeros([len(z),len(radec_indices_list)]),jnp.zeros([len(z),len(radec_indices_list)]),jnp.zeros([len(z),len(radec_indices_list)])
        
        for i,skypos in enumerate(radec_indices_list):
            gcp[:,i],bgp[:,i]=self.effective_galaxy_number_interpolant(z,skypos*jnp.ones_like(z).astype(int),cosmology)
            Mthr_array=self.calc_Mthr(z,jnp.ones_like(z,dtype=int)*skypos,cosmology)
            Mthr_array[z>self.z_grid[-1]]=-jnp.inf
            inco[:,i]=self.sch_fun.background_effective_galaxy_density(Mthr_array)/self.sch_fun.background_effective_galaxy_density(-jnp.ones_like(Mthr_array)*jnp.inf)
            
        fig,ax=plt.subplots(2,1,sharex=True)
        
        theo=self.sch_fun.background_effective_galaxy_density(-jnp.inf*jnp.ones_like(z))*cosmology.dVc_by_dzdOmega_at_z(z)
                
        ax[0].fill_between(jnp2onp(z),jnp2onp(jnp.percentile(gcp,5,axis=1)),jnp2onp(jnp.percentile(gcp,95,axis=1)),color='limegreen',alpha=0.2)
        ax[0].plot(jnp2onp(z),jnp2onp(jnp.median(gcp,axis=1)),label='Catalog part',color='limegreen',lw=2)
        
        ax[0].plot(jnp2onp(z),jnp2onp(jnp.median(bgp,axis=1)),label='Background part',color='slateblue',lw=2)
        
        ax[0].fill_between(jnp2onp(z),jnp2onp(jnp.percentile(bgp+gcp,5,axis=1)),jnp2onp(jnp.percentile(bgp+gcp,95,axis=1)),color='tomato',alpha=0.2)
        ax[0].plot(jnp2onp(z),jnp2onp(jnp.median(bgp+gcp,axis=1)),label='Sum',color='tomato',lw=2)        
        
        ax[0].plot(jnp2onp(z),jnp2onp(theo),label='Theoretical',color='k',lw=2,ls='--')
        ax[0].set_ylim([10,1e7])
        
        ax[0].legend()
        
        ax[0].set_yscale('log')
                
        ax[1].fill_between(jnp2onp(z),jnp2onp(jnp.percentile(1-inco,5,axis=1)),jnp2onp(jnp.percentile(1-inco,95,axis=1)),color='dodgerblue',alpha=0.5)
        ax[1].plot(jnp2onp(z),jnp2onp(jnp.median(1-inco,axis=1)),label='Completeness',color='dodgerblue',lw=1)
        ax[1].legend()
        
        return gcp,bgp,inco,fig,ax
        
 

    
    
    
    
    
    
    
