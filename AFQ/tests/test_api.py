import tempfile
import os
import os.path as op
import shutil

import numpy as np
import numpy.testing as npt

import pandas as pd
from pandas.testing import assert_frame_equal

import nibabel as nib
import nibabel.tmpdirs as nbtmp

import dipy.tracking.utils as dtu
import dipy.tracking.streamline as dts
import dipy.data as dpd
from dipy.data import fetcher
from dipy.io.streamline import save_tractogram

from AFQ import api
import AFQ.data as afd
import AFQ.segmentation as seg
import AFQ.utils.streamlines as aus


def touch(fname, times=None):
    with open(fname, 'a'):
        os.utime(fname, times)


def create_dummy_dmriprep_path(n_subjects, n_sessions):
    preproc_dir = tempfile.mkdtemp()
    subjects = ['sub-0%s' % (d + 1) for d in range(n_subjects)]
    sessions = ['sess-0%s' % (d + 1) for d in range(n_sessions)]
    for subject in subjects:
        for session in sessions:
            for modality in ['anat', 'dwi']:
                os.makedirs(op.join(preproc_dir, subject, session, modality))
            # Make some dummy data:
            aff = np.eye(4)
            data = np.ones((10, 10, 10, 6))
            bvecs = np.vstack([np.eye(3), np.eye(3)])
            bvecs[0] = 0
            bvals = np.ones(6) * 1000.
            bvals[0] = 0
            np.savetxt(op.join(preproc_dir, subject, session, 'dwi',
                               'dwi.bvals'),
                       bvals)
            np.savetxt(op.join(preproc_dir, subject, session, 'dwi',
                               'dwi.bvecs'),
                       bvecs)
            nib.save(nib.Nifti1Image(data, aff),
                     op.join(preproc_dir, subject, session, 'dwi',
                             'dwi.nii.gz'))
            nib.save(nib.Nifti1Image(data, aff),
                     op.join(preproc_dir, subject, session, 'anat',
                             'T1w.nii.gz'))
            nib.save(nib.Nifti1Image(data, aff),
                     op.join(preproc_dir, subject, session, 'anat',
                             'aparc+aseg.nii.gz'))

    return preproc_dir


def test_AFQ_init():
    """
    Test the initialization of the AFQ object
    """
    n_subjects = 3
    n_sessions = 2
    dmriprep_path = create_dummy_dmriprep_path(n_subjects, n_sessions)
    my_afq = api.AFQ(dmriprep_path=dmriprep_path)
    npt.assert_equal(my_afq.data_frame.shape, (n_subjects * n_sessions, 10))


def test_AFQ_data():
    """
    Test with some actual data
    """
    tmpdir = nbtmp.InTemporaryDirectory()
    afd.organize_stanford_data(path=tmpdir.name)
    myafq = api.AFQ(dmriprep_path=op.join(tmpdir.name, 'stanford_hardi',
                                        'derivatives', 'dmriprep'),
                    sub_prefix='sub')
    npt.assert_equal(nib.load(myafq.b0[0]).shape,
                     nib.load(myafq['dwi_file'][0]).shape[:3])
    npt.assert_equal(nib.load(myafq.b0[0]).shape,
                     nib.load(myafq.dti[0]).shape[:3])


def test_AFQ_data_planes():
    """
    Test with some actual data again, this time for track segmentation
    """
    tmpdir = nbtmp.InTemporaryDirectory()
    afd.organize_stanford_data(path=tmpdir.name)
    dmriprep_path = op.join(tmpdir.name, 'stanford_hardi',
                          'derivatives', 'dmriprep')
    seg_algo = "planes"
    bundle_names = ["SLF", "ARC", "CST", "FP"]
    myafq = api.AFQ(dmriprep_path=dmriprep_path,
                    sub_prefix='sub',
                    seg_algo=seg_algo,
                    bundle_names=bundle_names,
                    odf_model="DTI")

    # Replace the mapping and streamlines with precomputed:
    file_dict = afd.read_stanford_hardi_tractography()
    mapping = file_dict['mapping.nii.gz']
    streamlines = file_dict['tractography_subsampled.trk']
    streamlines = dts.Streamlines(
            dtu.move_streamlines([s for s in streamlines if s.shape[0] > 100],
                                 np.linalg.inv(myafq.dwi_affine[0])))

    sl_file = op.join(myafq.data_frame.results_dir[0],
                      'sub-01_sess-01_dwiDTI_det_streamlines.trk')
    save_tractogram(sl_file, streamlines, myafq.dwi_affine[0])

    mapping_file = op.join(myafq.data_frame.results_dir[0],
                                'sub-01_sess-01_dwi_mapping.nii.gz')
    nib.save(mapping, mapping_file)
    reg_prealign_file = op.join(myafq.data_frame.results_dir[0],
                                    'sub-01_sess-01_dwi_reg_prealign.npy')
    np.save(reg_prealign_file, np.eye(4))

    tgram = nib.streamlines.load(myafq.bundles[0]).tractogram
    bundles = aus.tgram_to_bundles(tgram, myafq.bundle_dict)
    npt.assert_(len(bundles['CST_L']) > 0)

    # Test ROI exporting:
    myafq.export_rois()
    assert op.exists(op.join(myafq.data_frame['results_dir'][0],
                     'ROIs',
                     'CST_R_roi1_include.nii.gz'))

    # Test bundles exporting:
    myafq.export_bundles()
    assert op.exists(op.join(myafq.data_frame['results_dir'][0],
                    'bundles',
                    'CST_R.trk'))

    tract_profiles = pd.read_csv(myafq.tract_profiles[0])
    assert tract_profiles.shape == (800, 5)


    # Before we run the CLI, we'll remove the bundles and ROI folders, to see
    # that the CLI generates them
    shutil.rmtree(op.join(myafq.data_frame['results_dir'][0],
                  'bundles'))

    shutil.rmtree(op.join(myafq.data_frame['results_dir'][0],
                  'ROIs'))

    # Test the CLI:
    print("Running the CLI:")
    cmd = "pyAFQ " + dmriprep_path
    out = os.system(cmd)
    assert out ==  0
    # The combined tract profiles should already exist from the CLI Run:
    from_file = pd.read_csv(op.join(myafq.afq_dir, 'tract_profiles.csv'))
    # And should be identical to what we would get by rerunning this:
    combined_profiles = myafq.combine_profiles()
    assert combined_profiles.shape == (800, 7)
    assert_frame_equal(combined_profiles, from_file)

    # Make sure the CLI did indeed generate these:
    assert op.exists(op.join(myafq.data_frame['results_dir'][0],
                     'ROIs',
                     'CST_R_roi1_include.nii.gz'))

    assert op.exists(op.join(myafq.data_frame['results_dir'][0],
                     'bundles',
                     'CST_R.trk'))


# def test_AFQ_data_recobundles():
#     tmpdir = nbtmp.InTemporaryDirectory()
#     afd.fetch_hcp(["100206"], hcp_bucket='hcp-openaccess', profile_name="hcp",
#                   path=tmpdir.name)
#     dmriprep_path = op.join(tmpdir.name, 'HCP', 'derivatives', 'dmriprep')
#     seg_algo = "recobundles"
#     bundle_names = ["F", "CST", "AF", "CC_ForcepsMajor"]
#     myafq = api.AFQ(dmriprep_path=dmriprep_path,
#                     sub_prefix='sub',
#                     seg_algo=seg_algo,
#                     bundle_names=bundle_names,
#                     odf_model="DTI",
#                     b0_threshold=15)

#     # Replace the streamlines with precomputed:
#     path_to_trk = dpd.fetcher.fetch_target_tractogram_hcp()
#     path_to_trk = dpd.fetcher.get_target_tractogram_hcp()
#     sl_file = op.join(myafq.data_frame.results_dir[0], 'sub-100206_sess-01_dwiDTI_det_streamlines.trk')
#     shutil.copy(path_to_trk, sl_file)
#     myafq.data_frame["streamlines_file"] = sl_file
#     print("here")
#     tgram = nib.streamlines.load(myafq.bundles[0]).tractogram
#     print("here")
#     bundles = aus.tgram_to_bundles(tgram, myafq.bundle_dict)
#     npt.assert_(len(bundles['CST_L']) > 0)