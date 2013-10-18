#!/usr/bin/env python
"""
This is the main program to run Mindboggle.

For help in using Mindboggle ::

    - Online `documentation <http://mindboggle.info/documentation.html>`_
    - README file
    - Help on the command line::

        >>> python mindboggler.py --help

This file uses Nipype (http://www.nipy.org/nipype/) to create a workflow
environment to enable Mindboggle to run in a flexible, modular manner
while storing provenance information.

Authors:
    - Arno Klein, 2010-2013  (arno@mindboggle.info)  http://binarybottle.com
    - Satrajit S. Ghosh, 2013  (satra@mit.edu)  http://www.mit.edu/~satra/
    - Each file lists Mindboggle team members who contributed to its content.

Copyright 2013,  Mindboggle team (http://mindboggle.info), Apache v2.0 License

"""

#=============================================================================
#
#   Import libraries
#
#=============================================================================
import os
import sys
import argparse
#-----------------------------------------------------------------------------
# Nipype libraries
#-----------------------------------------------------------------------------
from nipype.pipeline.engine import Workflow, Node
from nipype.interfaces.utility import Function as Fn
from nipype.interfaces.utility import IdentityInterface, Merge
from nipype.interfaces.io import DataGrabber, DataSink
from nipype.interfaces.freesurfer import MRIConvert
from nipype.interfaces.ants import Registration, ApplyTransforms
#-----------------------------------------------------------------------------
# Mindboggle libraries
#-----------------------------------------------------------------------------
from mindboggle.utils.utils import list_strings
from mindboggle.utils.io_vtk import read_vtk
from mindboggle.utils.io_table import write_columns, \
    write_shape_stats, write_vertex_measures
from mindboggle.DATA import hashes_url
from mindboggle.utils.io_uri import retrieve_data
from mindboggle.utils.compute import volume_per_label
from mindboggle.utils.mesh import rescale_by_neighborhood
from mindboggle.utils.freesurfer import surface_to_vtk, curvature_to_vtk, \
    annot_to_vtk, label_with_classifier, combine_segmentations
from mindboggle.utils.ants import fetch_ants_data, ComposeMultiTransform, \
    ImageMath, PropagateLabelsThroughMask, fill_volume_with_surface_labels, \
    ThresholdImage
from mindboggle.LABELS import dkt_protocol
from mindboggle.labels.relabel import relabel_surface, \
    keep_volume_labels, overwrite_volume_labels
from mindboggle.shapes.thickness import thickinthehead
from mindboggle.shapes.shape_tools import area, travel_depth, \
    geodesic_depth, curvature
from mindboggle.shapes.laplace_beltrami import spectrum_per_label
from mindboggle.shapes.zernike.zernike import zernike_moments_per_label
from mindboggle.shapes.likelihood import compute_likelihood
from mindboggle.features.folds import extract_folds
from mindboggle.features.sulci import extract_sulci
from mindboggle.features.fundi import extract_fundi, segment_fundi
from mindboggle.utils.paths import smooth_skeleton
from mindboggle.evaluate.evaluate_labels import measure_surface_overlap, \
    measure_volume_overlap

#=============================================================================
#
#   Command-line arguments
#
#=============================================================================
parser = argparse.ArgumentParser()
parser.add_argument("SUBJECTS",
                    help=('Example: "python %(prog)s sub1 sub2 sub3" '
                          '"sub1",... are subject names corresponding to '
                          'subject directories within $freesurfer_data'),
                    nargs='+') #, metavar='')
parser.add_argument("-o", help='Output directory: "-o $HOME/mindboggled" '
                               '(default)',
                    default=os.path.join(os.environ['HOME'], 'mindboggled'),
                    metavar='PATH')
parser.add_argument("-n",
                    help=('Number of processors: "-n 1" (default)'),
                    type=int,
                    default=1, metavar='INT')
#parser.add_argument("--run_freesurfer", action='store_true',
#                    help=("Run recon-all -all to generate expected "
#                          "FreeSurfer files, if not already done."))
#parser.add_argument("--run_ants", action='store_true',
#                    help=("Run antsCorticalThickness.sh to extract,"
#                          "segment, and register brain (and to provide"
#                          "an additional measure of cortical thickness)"))
parser.add_argument("--freesurfer_data",
                    help=("FreeSurfer subjects directory "
                          "(defaults to $SUBJECTS_DIR environment variable)"),
                    nargs='+', metavar='STR')
parser.add_argument("--ants_data",
                    help=("antsCorticalThickness.sh output directory "
                          "containing subject subdirectories "
                          "(otherwise use only FreeSurfer outputs)"),
                    nargs='+', metavar='STR')
parser.add_argument("--ants_stem",
                    help=("file stem for antsCorticalThickness.sh outputs "
                          "(otherwise use only FreeSurfer outputs)"),
                    nargs='+', metavar='STR')
parser.add_argument("--surface_labels",
                    help=("Source: {freesurfer (default), atlas, manual}; "
                          "Use 'atlas' if using FreeSurfer older than "
                          "version 5.3."),
                    choices=['freesurfer', 'atlas', 'manual'],
                    default='freesurfer', metavar='STR')
parser.add_argument("--atlases", help=("Label with extra volume "
                                       "atlas file(s) in MNI152 space"),
                    nargs='+', metavar='')
parser.add_argument("--sulci", action='store_true',
                    help="Extract, identify, and measure sulci")
parser.add_argument("--fundi", action='store_true',
                    help="Extract, identify, and measure fundi")
parser.add_argument("--spectra",
                    help='Number of Laplace-Beltrami spectrum eigenvalues '
                         'per label/feature to store in shape tables: '
                         '"--spectra 10" (default is not to run)',
                    default=0, type=int, metavar='INT')
parser.add_argument("--moments",
                    help='Order of Zernike moments per label/feature '
                         'to store in shape tables: "--moments 10" '
                         'is suggested but SLOW (default is not to run)',
                    default=0, type=int, metavar='INT')
parser.add_argument("--thickness", action='store_true',
                    help="Compute cortical label thicknesses with "
                         "thickinthehead()")
parser.add_argument("--antsurfer_labels", action='store_true',
                    help="Combine ANTs and FreeSurfer volume labels")
parser.add_argument("--no_volumes", action='store_true',
                    help="No volume labels, features, or shape tables")
parser.add_argument("--no_surfaces", action='store_true',
                    help="No surface labels, features, or shape tables")
parser.add_argument("--no_labels", action='store_true',
                    help="No surface or volume labels")
parser.add_argument("--no_shapes", action='store_true',
                    help="No shape tables of surface labels or features")
#parser.add_argument("--no_freesurfer_inputs", action='store_true',
#                    help="Don't use FreeSurfer (requires inputs -- UNTESTED)")
parser.add_argument("--vertices", action='store_true',
                    help=("Make table of per-vertex surface shape measures"))
parser.add_argument("--cluster", action='store_true',
                    help="Use HTCondor cluster (UNTESTED)")
parser.add_argument("--visual", help=('Generate py/graphviz workflow visual: '
                                      '{hier,flat,exec}'),
                    choices=['hier', 'flat', 'exec'], metavar='STR')
parser.add_argument("--version", help="Version number",
                    action='version', version='%(prog)s 0.1')
args = parser.parse_args()

#-----------------------------------------------------------------------------
# Data arguments:
#-----------------------------------------------------------------------------
#run_freesurfer = args.run_freesurfer
#run_ants = args.run_ants
subjects = args.SUBJECTS
do_ants = False
freesurfer_data = args.freesurfer_data
if not freesurfer_data:
    freesurfer_data = os.environ['SUBJECTS_DIR']
if args.ants_data:
    if len(args.ants_data) == 2:
        ants_data = args.ants_data[0]
        ants_stem = args.ants_data[1]
        do_ants = True
    else:
        sys.exit('--ants_data should be followed by two strings')
#-----------------------------------------------------------------------------
# Non-FreeSurfer data arguments:
#-----------------------------------------------------------------------------
do_input_vtk = False  # Load VTK surfaces directly (not FreeSurfer surfaces)
do_input_fs_labels = False  # Load nifti (not FreeSurfer mgh file)
use_FS_inputs = True
no_freesurfer_inputs = False #args.no_freesurfer_inputs
if no_freesurfer_inputs:
    use_FS_inputs = False
    do_input_vtk = True
    do_input_fs_labels = True
#-----------------------------------------------------------------------------
# Label and feature arguments:
#-----------------------------------------------------------------------------
surface_labels = args.surface_labels
no_surfaces = args.no_surfaces
no_volumes = args.no_volumes
do_sulci = args.sulci
do_fundi = args.fundi
no_labels = args.no_labels
do_smooth_fundi = False
antsurfer_labels = args.antsurfer_labels
if antsurfer_labels:
    no_surfaces = False
    no_volumes = False
    no_labels = False
do_label = False
do_surface = False
do_features = False
do_volumes = False
do_shapes = False
if not no_surfaces:
    do_surface = True
if not no_volumes:
    do_volumes = True
if not no_labels:
    do_label = True
if do_sulci or do_fundi:
    do_features = True
#-----------------------------------------------------------------------------
# Shape arguments:
#-----------------------------------------------------------------------------
no_shapes = args.no_shapes
do_spectra = False  # Measure Laplace-Beltrami spectra for labels/features
do_zernike = False  # Compute Zernike moments for labels/features
if not no_shapes:
    do_shapes = True
if (do_label or do_features) and do_shapes:
    if args.spectra > 0:
        do_spectra = True
    if args.moments > 0:
        do_zernike = True
do_freesurfer_thickness = False  # Include FreeSurfer's thickness measure
do_freesurfer_convexity = False  # Include FreeSurfer's convexity measure
if do_shapes and use_FS_inputs:
    do_freesurfer_thickness = True
    do_freesurfer_convexity = True

#=============================================================================
#
#   Hidden arguments: paths, label and template data
#
#=============================================================================
#-----------------------------------------------------------------------------
# Path to C++ code:
#-----------------------------------------------------------------------------
ccode_path = os.environ['MINDBOGGLE_TOOLS']  # Mindboggle C++ code directory
#-----------------------------------------------------------------------------
# Hashes to verify retrieved data, and cache and output directories:
#-----------------------------------------------------------------------------
hashes, url, cache_env, cache = hashes_url()
if cache_env in os.environ.keys():
    cache = os.environ[cache_env]
if not os.path.exists(cache):
    print("Create missing cache directory: {0}".format(cache))
    os.mkdir(cache)
temp_path = os.path.join(cache, 'workspace')  # Where to save workflow files
if not os.path.isdir(temp_path):
    os.makedirs(temp_path)
if not os.path.isdir(args.o):
    os.makedirs(args.o)
#-----------------------------------------------------------------------------
# Labeling protocol information:
#-----------------------------------------------------------------------------
sulcus_names, unique_sulcus_label_pairs, \
    sulcus_label_pair_lists, \
    left_sulcus_label_pair_lists, right_sulcus_label_pair_lists, \
    label_names, left_label_names, right_label_names, \
    label_numbers, left_label_numbers, right_label_numbers, \
    cortex_names, left_cortex_names, right_cortex_names, \
    cortex_numbers, left_cortex_numbers, right_cortex_numbers, \
    noncortex_names, left_noncortex_names, \
    right_noncortex_names, medial_noncortex_names, \
    noncortex_numbers, left_noncortex_numbers, \
    right_noncortex_numbers, medial_noncortex_numbers, \
    cortex_names_DKT25, \
    left_cortex_names_DKT25, right_cortex_names_DKT25, \
    cortex_numbers_DKT25, \
    left_cortex_numbers_DKT25, right_cortex_numbers_DKT25 = dkt_protocol()
#-----------------------------------------------------------------------------
# Volume atlases:
#-----------------------------------------------------------------------------
atlas_volumes = ['OASIS-TRT-20_jointfusion_DKT31_CMA_labels_in_MNI152.nii.gz']
atropos_to_MNI152_affine = 'OASIS-30_Atropos_template_to_MNI152_affine.txt'
if args.atlases:
    atlas_volumes.extend(args.atlases)
#-----------------------------------------------------------------------------
# Surface atlas labels:
# - 'manual': manual edits
# - FUTURE: <'adjusted': manual edits after automated alignment to fundi>
#-----------------------------------------------------------------------------
surface_classifier = 'DKTatlas40'
surface_atlas_type = 'manual'
#-----------------------------------------------------------------------------
# Evaluation
#-----------------------------------------------------------------------------
do_evaluate_surf_labels = False  # Surface overlap: auto vs. manual labels
do_evaluate_vol_labels = False  # Volume overlap: auto vs. manual labels

#=============================================================================
#
#   Initialize workflow inputs and outputs
#
#=============================================================================
mbFlow = Workflow(name='Mindboggle')
mbFlow.base_dir = temp_path
#-----------------------------------------------------------------------------
# Iterate inputs over subjects, hemispheres, and atlases
# (surfaces are assumed to take the form: lh.pial or lh.pial.vtk)
#-----------------------------------------------------------------------------
if isinstance(atlas_volumes, str):
    atlas_volumes = list(atlas_volumes)
InputVolAtlases = Node(name='Input_volume_atlases',
                       interface=IdentityInterface(fields=['atlas']))
InputVolAtlases.iterables = ('atlas', atlas_volumes)
InputSubjects = Node(name='Input_subjects',
                     interface=IdentityInterface(fields=['subject']))
InputSubjects.iterables = ('subject', subjects)
InputHemis = Node(name='Input_hemispheres',
                  interface=IdentityInterface(fields=['hemi']))
InputHemis.iterables = ('hemi', ['lh', 'rh'])
#-----------------------------------------------------------------------------
# Outputs
#-----------------------------------------------------------------------------
Sink = Node(DataSink(), name='Results')
Sink.inputs.base_directory = args.o
Sink.inputs.container = ''
Sink.inputs.substitutions = [('_hemi_lh', 'left_surface'),
    ('_hemi_rh', 'right_surface'),
    ('_subject_', ''),
    ('_atlas_', ''),
    ('smooth_skeletons.vtk', 'smooth_fundi.vtk'),
    ('OASIS-TRT-20_jointfusion_DKT31_CMA_labels_in_MNI152.nii.gz', 'atlas')]
    #,
    #('PropagateLabelsThroughMask.nii.gz', 'labels.nii.gz'),
    #('overwrite_volume_labels.nii.gz',
    # 'cortical_surface_and_noncortical_volume_labels.nii.gz'),
    #]
#-----------------------------------------------------------------------------
# ANTs transforms for saving MNI152-transformed coordinates and for labeling
#-----------------------------------------------------------------------------
if do_ants:
    #-------------------------------------------------------------------------
    # Retrieve ANTs data:
    #-------------------------------------------------------------------------
    FetchANTs = Node(name='Fetch_ants_data',
                     interface=Fn(function=fetch_ants_data,
                                  input_names=['subjects_dir',
                                               'subject',
                                               'stem'],
                                  output_names=['brain',
                                                'segments',
                                                'affine',
                                                'warp',
                                                'invwarp']))
    mbFlow.add_nodes([FetchANTs])
    FetchANTs.inputs.subjects_dir = ants_data
    mbFlow.connect(InputSubjects, 'subject', FetchANTs, 'subject')
    FetchANTs.inputs.stem = ants_stem
    #-------------------------------------------------------------------------
    # Retrieve full atlas path(s):
    #-------------------------------------------------------------------------
    FetchAtlas = Node(name='Fetch_atlas',
                      interface=Fn(function=retrieve_data,
                                   input_names=['data_file',
                                                'url',
                                                'hashes',
                                                'cache_env',
                                                'cache',
                                                'return_missing'],
                                   output_names=['data_path']))
    mbFlow.add_nodes([FetchAtlas])
    mbFlow.connect(InputVolAtlases, 'atlas', FetchAtlas, 'data_file')
    FetchAtlas.inputs.url = url
    FetchAtlas.inputs.hashes = hashes
    FetchAtlas.inputs.cache_env = cache_env
    FetchAtlas.inputs.cache = cache
    FetchAtlas.inputs.return_missing = True
    #-------------------------------------------------------------------------
    # Compose single affine transform from subject to MNI152:
    #-------------------------------------------------------------------------
    if do_shapes or do_label:
        affine_to_mni = retrieve_data(atropos_to_MNI152_affine,
                                      url, hashes, cache_env, cache)
    if do_shapes:
        AffineFileList = Node(name='Merge_affine_file_list',
                              interface=Fn(function=list_strings,
                                           input_names=['string1',
                                                        'string2',
                                                        'string3',
                                                        'string4'],
                                           output_names=['string_list']))
        AffineFileList.inputs.string1 = affine_to_mni
        mbFlow.connect(FetchANTs, 'affine', AffineFileList, 'string2')
        AffineFileList.inputs.string3 = ''
        AffineFileList.inputs.string4 = ''

        ComposeAffine = Node(name='Compose_affine_transform',
                             interface=Fn(function=ComposeMultiTransform,
                                          input_names=['transform_files',
                                                       'inverse_Booleans',
                                                       'output_transform_file',
                                                       'ext'],
                                          output_names=['output_transform_file']))
        mbFlow.add_nodes([ComposeAffine])
        mbFlow.connect(AffineFileList, 'string_list',
                       ComposeAffine, 'transform_files')
        ComposeAffine.inputs.inverse_Booleans = [False, False]
        ComposeAffine.inputs.output_transform_file = ''
        ComposeAffine.inputs.ext = '.txt'
    #-------------------------------------------------------------------------
    # Construct ANTs MNI152-to-subject nonlinear transform lists:
    #-------------------------------------------------------------------------
    if do_label:
        WarpToSubjectFileList = Node(name='Merge_warp_file_list',
                                     interface=Fn(function=list_strings,
                                          input_names=['string1',
                                                       'string2',
                                                       'string3',
                                                       'string4'],
                                          output_names=['string_list']))
        mbFlow.connect(FetchANTs, 'affine', WarpToSubjectFileList, 'string1')
        mbFlow.connect(FetchANTs, 'invwarp', WarpToSubjectFileList, 'string2')
        WarpToSubjectFileList.inputs.string3 = affine_to_mni
        WarpToSubjectFileList.inputs.string4 = ''
        warp_inverse_Booleans = [True, False, True]

#=============================================================================
#-----------------------------------------------------------------------------
#
#   Surface workflows
#
#-----------------------------------------------------------------------------
#=============================================================================
if do_surface:
    #-------------------------------------------------------------------------
    # Location and structure of the surface inputs:
    #-------------------------------------------------------------------------
    use_white_surface = False
    if use_white_surface:
        Surf = Node(name='Surfaces',
                    interface=DataGrabber(infields=['subject', 'hemi'],
                                          outfields=['surface_files',
                                                     'white_surface_files'],
                                          sort_filelist=False))
    else:
        Surf = Node(name='Surfaces',
                    interface=DataGrabber(infields=['subject', 'hemi'],
                                          outfields=['surface_files'],
                                          sort_filelist=False))
    Surf.inputs.base_directory = freesurfer_data
    Surf.inputs.template = '%s/surf/%s.%s'
    Surf.inputs.template_args['surface_files'] = [['subject', 'hemi', 'pial']]
    if use_white_surface:
        Surf.inputs.template_args['white_surface_files'] = [['subject',
                                                             'hemi', 'white']]
    #Surf.inputs.template_args['sphere_files'] = [['subject','hemi','sphere']]
    if do_freesurfer_thickness:
        Surf.inputs.template_args['freesurfer_thickness_files'] = \
            [['subject', 'hemi', 'thickness']]
    if do_freesurfer_convexity:
        Surf.inputs.template_args['freesurfer_convexity_files'] = \
            [['subject', 'hemi', 'sulc']]
    
    mbFlow.connect(InputSubjects, 'subject', Surf, 'subject')
    mbFlow.connect(InputHemis, 'hemi', Surf, 'hemi')
    #-------------------------------------------------------------------------
    # Convert surfaces to VTK:
    #-------------------------------------------------------------------------
    if not do_input_vtk:
        ConvertSurf = Node(name='Surface_to_vtk',
                           interface=Fn(function=surface_to_vtk,
                                        input_names=['surface_file',
                                                     'output_vtk'],
                                        output_names=['output_vtk']))
        mbFlow.connect(Surf, 'surface_files', ConvertSurf, 'surface_file')
        ConvertSurf.inputs.output_vtk = ''
        if use_white_surface:
            ConvertWhiteSurf = ConvertSurf.clone('Gray-white_surface_to_vtk')
            mbFlow.add_nodes([ConvertWhiteSurf])
            mbFlow.connect(Surf, 'white_surface_files',
                           ConvertWhiteSurf, 'surface_file')
    #-------------------------------------------------------------------------
    # Evaluation inputs: location and structure of atlas surfaces:
    #-------------------------------------------------------------------------
    if (do_evaluate_surf_labels or surface_labels == 'manual') and do_label:
        SurfaceAtlas = Node(name='Surface_atlas',
                            interface=DataGrabber(infields=['subject','hemi'],
                                                  outfields=['atlas_file'],
                                                  sort_filelist=False))
        SurfaceAtlas.inputs.base_directory = freesurfer_data
        SurfaceAtlas.inputs.template = '%s/label/%s.labels.DKT31.' +\
                                       surface_atlas_type + '.vtk'
        SurfaceAtlas.inputs.template_args['atlas_file'] = [['subject','hemi']]
    
        mbFlow.connect(InputSubjects, 'subject', SurfaceAtlas, 'subject')
        mbFlow.connect(InputHemis, 'hemi', SurfaceAtlas, 'hemi')
    
    #=========================================================================
    #
    #   Surface labels
    #
    #=========================================================================
    if do_label:
        SurfLabelFlow = Workflow(name='Surface_labels')
    
        #=====================================================================
        # Initialize labels with the DKT classifier atlas
        #=====================================================================
        if surface_labels == 'atlas' and use_FS_inputs:
            #-----------------------------------------------------------------
            # Label brain with DKT atlas using FreeSurfer's mris_ca_label:
            #-----------------------------------------------------------------
            Classifier = Node(name='mris_ca_label',
                              interface=Fn(function=label_with_classifier,
                                           input_names=['subject',
                                                        'hemi',
                                                        'left_classifier',
                                                        'right_classifier',
                                                        'annot_file'],
                                           output_names=['annot_file']))
            SurfLabelFlow.add_nodes([Classifier])
            mbFlow.connect(InputSubjects, 'subject',
                           SurfLabelFlow, 'mris_ca_label.subject')
            mbFlow.connect(InputHemis, 'hemi',
                           SurfLabelFlow, 'mris_ca_label.hemi')
            left_classifier_file = 'lh.' + surface_classifier + '.gcs'
            right_classifier_file = 'rh.' + surface_classifier + '.gcs'
            left_classifier = retrieve_data(left_classifier_file, url,
                                            hashes, cache_env, cache)
            right_classifier = retrieve_data(right_classifier_file, url,
                                             hashes, cache_env, cache)
            Classifier.inputs.left_classifier = left_classifier
            Classifier.inputs.right_classifier = right_classifier
            Classifier.inputs.annot_file = ''
            #-----------------------------------------------------------------
            # Convert .annot file to VTK format:
            #-----------------------------------------------------------------
            Classifier2vtk = Node(name='annot_to_vtk',
                                  interface=Fn(function=annot_to_vtk,
                                               input_names=['annot_file',
                                                            'vtk_file'],
                                               output_names=['labels',
                                                             'output_vtk']))
            SurfLabelFlow.add_nodes([Classifier2vtk])
            SurfLabelFlow.connect(Classifier, 'annot_file',
                                  Classifier2vtk, 'annot_file')
            if do_input_vtk:
                mbFlow.connect(Surf, 'surface_files',
                               SurfLabelFlow, 'annot_to_vtk.vtk_file')
            else:
                mbFlow.connect(ConvertSurf, 'output_vtk',
                               SurfLabelFlow, 'annot_to_vtk.vtk_file')
            mbFlow.connect(SurfLabelFlow, 'annot_to_vtk.output_vtk',
                           Sink, 'labels.@DKT_surface')
            plug = 'annot_to_vtk.output_vtk'
            plug1 = Classifier2vtk
            plug2 = 'output_vtk'
    
        #=====================================================================
        # Initialize labels with FreeSurfer
        #=====================================================================
        elif surface_labels == 'freesurfer' and use_FS_inputs:
            #-----------------------------------------------------------------
            # Location and structure of the FreeSurfer label inputs:
            #-----------------------------------------------------------------
            if use_FS_inputs and do_label and surface_labels == 'freesurfer':
                Annot = Node(name='annot',
                             interface=DataGrabber(infields=['subject', 
                                                             'hemi'],
                                                   outfields=['annot_files'],
                                                   sort_filelist=False))
                Annot.inputs.base_directory = freesurfer_data
                Annot.inputs.template = '%s/label/%s.aparc.annot'
                Annot.inputs.template_args['annot_files'] = [['subject', 
                                                              'hemi']]
                mbFlow.connect(InputSubjects, 'subject', Annot, 'subject')
                mbFlow.connect(InputHemis, 'hemi', Annot, 'hemi')
            #-----------------------------------------------------------------
            # Convert Annot to VTK format:
            #-----------------------------------------------------------------
            FreeLabels = Node(name='FreeSurfer_annot_to_vtk',
                              interface=Fn(function=annot_to_vtk,
                                           input_names=['annot_file',
                                                        'vtk_file'],
                                           output_names=['labels',
                                                         'output_vtk']))
            SurfLabelFlow.add_nodes([FreeLabels])
            mbFlow.connect(Annot, 'annot_files', SurfLabelFlow, 
                           'FreeSurfer_annot_to_vtk.annot_file')
            if do_input_vtk:
                mbFlow.connect(Surf, 'surface_files', SurfLabelFlow, 
                               'FreeSurfer_annot_to_vtk.vtk_file')
            else:
                mbFlow.connect(ConvertSurf, 'output_vtk', SurfLabelFlow, 
                               'FreeSurfer_annot_to_vtk.vtk_file')
            mbFlow.connect(SurfLabelFlow, 
                           'FreeSurfer_annot_to_vtk.output_vtk',
                           Sink, 'labels.@Free_surface')
            plug = 'FreeSurfer_annot_to_vtk.output_vtk'
            plug1 = FreeLabels
            plug2 = 'output_vtk'
    
        #=====================================================================
        # Skip label initialization and process manual (atlas) labels
        #=====================================================================
        elif surface_labels == 'manual':
            ManualSurfLabels = Node(name='Manual_surface_labels',
                                    interface=Fn(function=read_vtk,
                                                 input_names=['input_vtk',
                                                              'return_first',
                                                              'return_array'],
                                                 output_names=['faces',
                                                               'lines',
                                                               'indices',
                                                               'points',
                                                               'npoints',
                                                               'scalars',
                                                               'scalar_names',
                                                               'input_vtk']))
            SurfLabelFlow.add_nodes([ManualSurfLabels])
            mbFlow.connect(SurfaceAtlas, 'atlas_file',
                           SurfLabelFlow, 'Manual_surface_labels.input_vtk')
            ManualSurfLabels.inputs.return_first = 'True'
            ManualSurfLabels.inputs.return_array = 'False'
            plug = 'Manual_surface_labels.input_vtk'
            plug1 = ManualSurfLabels
            plug2 = 'input_vtk'
    
        ##=====================================================================
        ## Surface label evaluation against manual labels
        ##=====================================================================
        #if do_evaluate_surf_labels:
        #
        #    EvalSurfLabels = Node(name='Evaluate_surface_labels',
        #                          interface=Fn(function=measure_surface_overlap,
        #                                       input_names=['command',
        #                                                    'labels_file1',
        #                                                    'labels_file2'],
        #                                       output_names=['overlap_file']))
        #    mbFlow.add_nodes([EvalSurfLabels])
        #    surface_overlap_command = os.path.join(ccode_path,
        #        'surface_overlap', 'SurfaceOverlapMain')
        #    EvalSurfLabels.inputs.command = surface_overlap_command
        #    mbFlow.connect(SurfaceAtlas, 'atlas_file',
        #                   EvalSurfLabels, 'labels_file1')
        #    mbFlow.connect(SurfLabelFlow, plug,
        #                   'EvalSurfLabels.labels_file2')
    
        #=====================================================================
        # Convert surface label numbers to volume label numbers
        #=====================================================================
        ReindexLabels = Node(name='Reindex_labels',
                             interface=Fn(function=relabel_surface,
                                          input_names=['vtk_file',
                                                       'hemi',
                                                       'old_labels',
                                                       'new_labels',
                                                       'output_file'],
                                          output_names=['output_file']))
        SurfLabelFlow.add_nodes([ReindexLabels])
        SurfLabelFlow.connect(plug1, plug2, ReindexLabels, 'vtk_file')
        mbFlow.connect(InputHemis, 'hemi',
                       SurfLabelFlow, 'Reindex_labels.hemi')
        ReindexLabels.inputs.old_labels = ''
        ReindexLabels.inputs.new_labels = ''
        ReindexLabels.inputs.output_file = ''
        mbFlow.connect(SurfLabelFlow, 'Reindex_labels.output_file',
                       Sink, 'labels.@surface')
    
    #=========================================================================
    #
    #   Surface shape measurements
    #
    #=========================================================================
    if do_shapes:
        WholeSurfShapeFlow = Workflow(name='Surface_shapes')
        #---------------------------------------------------------------------
        # Measure surface area:
        #---------------------------------------------------------------------
        SurfaceArea = Node(name='Surface_area',
                    interface=Fn(function=area,
                                 input_names=['command',
                                              'surface_file'],
                                 output_names=['area_file']))
        area_command = os.path.join(ccode_path, 'area', 'PointAreaMain')
        SurfaceArea.inputs.command = area_command
        #---------------------------------------------------------------------
        # Measure surface travel depth:
        #---------------------------------------------------------------------
        TravelDepth = Node(name='Travel_depth',
                           interface=Fn(function=travel_depth,
                                        input_names=['command',
                                                     'surface_file'],
                                        output_names=['depth_file']))
        WholeSurfShapeFlow.add_nodes([TravelDepth])
        TravelDepth.inputs.command = os.path.join(ccode_path,
                                                  'travel_depth',
                                                  'TravelDepthMain')
        #---------------------------------------------------------------------
        # Rescale surface travel depth:
        #---------------------------------------------------------------------
        if do_fundi:
            RescaleTravelDepth = Node(name='Rescale_travel_depth',
                                interface=Fn(function=rescale_by_neighborhood,
                                     input_names=['input_vtk',
                                                  'indices',
                                                  'nedges',
                                                  'p',
                                                  'set_max_to_1',
                                                  'save_file',
                                                  'output_filestring'],
                                     output_names=['rescaled_scalars',
                                                   'rescaled_scalars_file']))
            WholeSurfShapeFlow.add_nodes([RescaleTravelDepth])
            WholeSurfShapeFlow.connect(TravelDepth, 'depth_file',
                                       RescaleTravelDepth, 'input_vtk')
            RescaleTravelDepth.inputs.indices = []
            RescaleTravelDepth.inputs.nedges = 10
            RescaleTravelDepth.inputs.p = 99
            RescaleTravelDepth.inputs.set_max_to_1 = True
            RescaleTravelDepth.inputs.save_file = True
            RescaleTravelDepth.inputs.output_filestring = \
                'travel_depth_rescaled'
        #---------------------------------------------------------------------
        # Measure surface geodesic depth:
        #---------------------------------------------------------------------
        GeodesicDepth = Node(name='Geodesic_depth',
                             interface=Fn(function=geodesic_depth,
                                          input_names=['command',
                                                       'surface_file'],
                                          output_names=['depth_file']))
        GeodesicDepth.inputs.command = os.path.join(ccode_path,
                                                    'geodesic_depth',
                                                    'GeodesicDepthMain')
        #---------------------------------------------------------------------
        # Measure surface curvature:
        #---------------------------------------------------------------------
        CurvNode = Node(name='Curvature',
                        interface=Fn(function=curvature,
                             input_names=['command',
                                          'method',
                                          'arguments',
                                          'surface_file'],
                             output_names=['mean_curvature_file',
                                           'gauss_curvature_file',
                                           'max_curvature_file',
                                           'min_curvature_file',
                                           'min_curvature_vector_file']))
        CurvNode.inputs.command = os.path.join(ccode_path,
                                               'curvature',
                                               'CurvatureMain')
        CurvNode.inputs.method = 2
        CurvNode.inputs.arguments = '-n 0.7'
        #---------------------------------------------------------------------
        # Convert FreeSurfer surface measures to VTK:
        #---------------------------------------------------------------------
        if do_freesurfer_convexity:
            ConvexNode = Node(name='Convexity_to_vtk',
                              interface=Fn(function=curvature_to_vtk,
                                           input_names=['surface_file',
                                                        'vtk_file',
                                                        'output_vtk'],
                                           output_names=['output_vtk']))
            WholeSurfShapeFlow.add_nodes([ConvexNode])
            mbFlow.connect(Surf, 'freesurfer_convexity_files',
                           WholeSurfShapeFlow, 
                           'Convexity_to_vtk.surface_file')
            mbFlow.connect(ConvertSurf, 'output_vtk',
                           WholeSurfShapeFlow, 'Convexity_to_vtk.vtk_file')
            ConvexNode.inputs.output_vtk = ''
            mbFlow.connect(WholeSurfShapeFlow, 'Convexity_to_vtk.output_vtk',
                           Sink, 'shapes.@freesurfer_convexity')
        if do_freesurfer_thickness:
            ThickNode = Node(name='Thickness_to_vtk',
                             interface=Fn(function=curvature_to_vtk,
                                          input_names=['surface_file',
                                                       'vtk_file',
                                                       'output_vtk'],
                                          output_names=['output_vtk']))
            WholeSurfShapeFlow.add_nodes([ThickNode])
            mbFlow.connect(Surf, 'freesurfer_thickness_files',
                           WholeSurfShapeFlow, 
                           'Thickness_to_vtk.surface_file')
            mbFlow.connect(ConvertSurf, 'output_vtk',
                           WholeSurfShapeFlow, 'Thickness_to_vtk.vtk_file')
            ThickNode.inputs.output_vtk = ''
            mbFlow.connect(WholeSurfShapeFlow, 'Thickness_to_vtk.output_vtk',
                           Sink, 'shapes.@freesurfer_thickness')
        #---------------------------------------------------------------------
        # Connect nodes:
        #---------------------------------------------------------------------
        WholeSurfShapeFlow.add_nodes([SurfaceArea, GeodesicDepth, CurvNode])
        if do_input_vtk:
            mbFlow.connect([(Surf, WholeSurfShapeFlow,
                             [('surface_files','Surface_area.surface_file'),
                              ('surface_files','Travel_depth.surface_file'),
                              ('surface_files','Geodesic_depth.surface_file'),
                              ('surface_files','Curvature.surface_file')])])
        else:
            mbFlow.connect([(ConvertSurf, WholeSurfShapeFlow,
                               [('output_vtk', 'Surface_area.surface_file'),
                                ('output_vtk', 'Travel_depth.surface_file'),
                                ('output_vtk', 'Geodesic_depth.surface_file'),
                                ('output_vtk', 'Curvature.surface_file')])])
        mbFlow.connect([(WholeSurfShapeFlow, Sink,
           [('Surface_area.area_file', 'shapes.@surface_area'),
            ('Travel_depth.depth_file', 'shapes.@travel_depth'),
            ('Geodesic_depth.depth_file', 'shapes.@geodesic_depth'),
            ('Curvature.mean_curvature_file', 'shapes.@mean_curvature')])])
    
    #=========================================================================
    #
    #   Surface feature extraction
    #
    #=========================================================================
    if do_features:
        SurfFeatureFlow = Workflow(name='Surface_features')
    
        #=====================================================================
        # Folds and sulci
        #=====================================================================
        if do_sulci:
            #-----------------------------------------------------------------
            # Folds:
            #-----------------------------------------------------------------
            FoldsNode = Node(name='Folds',
                             interface=Fn(function=extract_folds,
                                          input_names=['depth_file',
                                                       'min_fold_size',
                                                       'tiny_depth',
                                                       'save_file'],
                                          output_names=['folds',
                                                        'n_folds',
                                                        'depth_threshold',
                                                        'bins',
                                                        'bin_edges',
                                                        'folds_file']))
            SurfFeatureFlow.add_nodes([FoldsNode])
            mbFlow.connect(WholeSurfShapeFlow, 'Travel_depth.depth_file',
                             SurfFeatureFlow, 'Folds.depth_file')
            FoldsNode.inputs.min_fold_size = 50
            FoldsNode.inputs.tiny_depth = 0.001
            FoldsNode.inputs.save_file = True
            mbFlow.connect(SurfFeatureFlow, 'Folds.folds_file',
                           Sink, 'features.@folds')    
            #-----------------------------------------------------------------
            # Sulci:
            #-----------------------------------------------------------------
            SulciNode = Node(name='Sulci',
                             interface=Fn(function=extract_sulci,
                                          input_names=['labels_file',
                                                       'folds_or_file',
                                                       'hemi',
                                                       'min_boundary',
                                                       'sulcus_names'],
                                          output_names=['sulci',
                                                        'n_sulci',
                                                        'sulci_file']))
            SurfFeatureFlow.add_nodes([SulciNode])
            mbFlow.connect(SurfLabelFlow, 'Reindex_labels.output_file',
                           SurfFeatureFlow, 'Sulci.labels_file')
            SurfFeatureFlow.connect(FoldsNode, 'folds',
                                    SulciNode, 'folds_or_file')
            mbFlow.connect(InputHemis, 'hemi', SurfFeatureFlow, 'Sulci.hemi')
            SulciNode.inputs.min_boundary = 1
            SulciNode.inputs.sulcus_names = sulcus_names
            mbFlow.connect(SurfFeatureFlow, 'Sulci.sulci_file',
                           Sink, 'features.@sulci')
    
        #=====================================================================
        # Fundi
        #=====================================================================
        if do_fundi:
            #-----------------------------------------------------------------
            # Extract a fundus per fold:
            #-----------------------------------------------------------------
            FoldFundi = Node(name='Fundus_per_fold',
                             interface=Fn(function=extract_fundi,
                                  input_names=['folds',
                                               'curv_file',
                                               'depth_file',
                                               'min_separation',
                                               'erode_ratio',
                                               'erode_min_size',
                                               'save_file'],
                                  output_names=['fundus_per_fold',
                                                'n_fundi_in_folds',
                                                'fundus_per_fold_file']))
            SurfFeatureFlow.connect(FoldsNode, 'folds', FoldFundi, 'folds')
            mbFlow.connect([(WholeSurfShapeFlow, SurfFeatureFlow,
                           [('Curvature.mean_curvature_file',
                             'Fundus_per_fold.curv_file'),
                            ('Rescale_travel_depth.rescaled_scalars_file',
                             'Fundus_per_fold.depth_file')])])
            FoldFundi.inputs.min_separation = 10
            FoldFundi.inputs.erode_ratio = 0.10
            FoldFundi.inputs.erode_min_size = 10
            FoldFundi.inputs.save_file = True
            mbFlow.connect(SurfFeatureFlow,
                           'Fundus_per_fold.fundus_per_fold_file',
                           Sink, 'features.@fundus_per_fold')

            if do_smooth_fundi:
                #-------------------------------------------------------------
                # Compute likelihoods for smoothing fundi:
                #-------------------------------------------------------------
                LikelihoodNode = Node(name='Likelihood',
                    interface=Fn(function=compute_likelihood,
                                 input_names=['trained_file',
                                              'depth_file',
                                              'curvature_file',
                                              'folds',
                                              'save_file'],
                                 output_names=['likelihoods',
                                               'likelihoods_file']))
                SurfFeatureFlow.add_nodes([LikelihoodNode])
                border_params_file = \
                    'depth_curv_border_nonborder_parameters.pkl'
                border_params_path = retrieve_data(border_params_file, url,
                                                   hashes, cache_env, cache)
                LikelihoodNode.inputs.trained_file = border_params_path
                mbFlow.connect([(WholeSurfShapeFlow, SurfFeatureFlow,
                    [('Rescale_travel_depth.rescaled_scalars_file',
                      'Likelihood.depth_file'),
                     ('Curvature.mean_curvature_file',
                      'Likelihood.curvature_file')])])
                SurfFeatureFlow.connect(FoldsNode, 'folds',
                                        LikelihoodNode, 'folds')
                LikelihoodNode.inputs.save_file = True
                #mbFlow.connect(SurfFeatureFlow, 'Likelihood.likelihoods_file',
                #               Sink, 'features.@likelihoods')
                #-------------------------------------------------------------
                # Smooth fundi:
                #-------------------------------------------------------------
                SmoothFundi = Node(name='Smooth_fundi',
                                   interface=Fn(function=smooth_skeleton,
                                        input_names=['skeletons',
                                                     'bounds',
                                                     'vtk_file',
                                                     'likelihoods',
                                                     'wN_max',
                                                     'erode_again',
                                                     'save_file'],
                                        output_names=['smooth_skeletons',
                                                      'n_skeletons',
                                                      'skeletons_file']))
                SurfFeatureFlow.connect(FoldFundi, 'fundus_per_fold',
                                        SmoothFundi, 'skeletons')
                SurfFeatureFlow.connect(FoldsNode, 'folds',
                                        SmoothFundi, 'bounds')
                mbFlow.connect(WholeSurfShapeFlow,
                               'Curvature.mean_curvature_file',
                               SurfFeatureFlow, 'Smooth_fundi.vtk_file')
                SurfFeatureFlow.connect(LikelihoodNode, 'likelihoods',
                                        SmoothFundi, 'likelihoods')
                SmoothFundi.inputs.wN_max = 1.0
                SmoothFundi.inputs.erode_again = False
                SmoothFundi.inputs.save_file = True
                mbFlow.connect(SurfFeatureFlow, 'Smooth_fundi.skeletons_file',
                               Sink, 'features.@smooth_fundi')

            #-----------------------------------------------------------------
            # Segment a fundus per sulcus:
            #-----------------------------------------------------------------
            SulcusFundi = Node(name='Fundus_per_sulcus',
                               interface=Fn(function=segment_fundi,
                                    input_names=['fundus_per_fold',
                                                 'sulci',
                                                 'vtk_file',
                                                 'save_file'],
                                    output_names=['fundus_per_sulcus',
                                                  'n_fundi',
                                                  'fundus_per_sulcus_file']))
            if do_smooth_fundi:
                SurfFeatureFlow.connect(SmoothFundi, 'smooth_skeletons',
                                        SulcusFundi, 'fundus_per_fold')
            else:
                SurfFeatureFlow.connect(FoldFundi, 'fundus_per_fold',
                                        SulcusFundi, 'fundus_per_fold')
            SurfFeatureFlow.connect(SulciNode, 'sulci', SulcusFundi, 'sulci')
            mbFlow.connect(WholeSurfShapeFlow,
                           'Curvature.mean_curvature_file',
                           SurfFeatureFlow, 'Fundus_per_sulcus.vtk_file')
            SulcusFundi.inputs.save_file = True
            mbFlow.connect(SurfFeatureFlow,
                           'Fundus_per_sulcus.fundus_per_sulcus_file',
                           Sink, 'features.@fundus_per_sulcus')

    #=========================================================================
    #
    #   Surface feature shapes
    #
    #=========================================================================
    if do_shapes:
        SurfFeatureShapeFlow = Workflow(name='Surface_feature_shapes')
        #=====================================================================
        # Compute Laplace-Beltrami spectra
        #=====================================================================
        if do_spectra:
            #-----------------------------------------------------------------
            # Measure spectra of labeled regions:
            #-----------------------------------------------------------------
            SpectraLabels = Node(name='Spectra_labels',
                                 interface=Fn(function=spectrum_per_label,
                                              input_names=['vtk_file',
                                                           'spectrum_size',
                                                           'exclude_labels',
                                                           'normalization',
                                                           'area_file',
                                                           'largest_segment'],
                                              output_names=['spectrum_lists',
                                                            'label_list']))
            SurfFeatureShapeFlow.add_nodes([SpectraLabels])
            mbFlow.connect(SurfLabelFlow, 'Reindex_labels.output_file',
                           SurfFeatureShapeFlow, 'Spectra_labels.vtk_file')
            SpectraLabels.inputs.spectrum_size = args.spectra
            SpectraLabels.inputs.exclude_labels = [0]
            SpectraLabels.inputs.normalization = "area"
            SpectraLabels.inputs.area_file = ""
            SpectraLabels.inputs.largest_segment = True
            mbFlow.connect(WholeSurfShapeFlow, 'Surface_area.area_file',
                           SurfFeatureShapeFlow, 'Spectra_labels.area_file')
            #-----------------------------------------------------------------
            # Compute spectra of sulci:
            #-----------------------------------------------------------------
            if do_sulci:
                SpectraSulci = SpectraLabels.clone('Spectra_sulci')
                SurfFeatureShapeFlow.add_nodes([SpectraSulci])
                mbFlow.connect(SurfFeatureFlow, 'Sulci.sulci_file',
                               SurfFeatureShapeFlow, 'Spectra_sulci.vtk_file')
                SpectraSulci.inputs.exclude_labels = [-1]
    
        #=====================================================================
        # Compute Zernike moments
        #=====================================================================
        if do_zernike:
            #-----------------------------------------------------------------
            # Measure Zernike moments of labeled regions:
            #-----------------------------------------------------------------
            ZernikeLabels = Node(name='Zernike_labels',
                 interface=Fn(function=zernike_moments_per_label,
                              input_names=['vtk_file',
                                           'order',
                                           'exclude_labels',
                                           'scale_input',
                                           'decimate_fraction',
                                           'decimate_smooth'],
                              output_names=['descriptors_lists',
                                            'label_list']))
            SurfFeatureShapeFlow.add_nodes([ZernikeLabels])
            mbFlow.connect(SurfLabelFlow, 'Reindex_labels.output_file',
                           SurfFeatureShapeFlow, 'Zernike_labels.vtk_file')
            ZernikeLabels.inputs.order = args.moments
            ZernikeLabels.inputs.exclude_labels = [0]
            ZernikeLabels.inputs.scale_input = True
            ZernikeLabels.inputs.decimate_fraction = 0
            ZernikeLabels.inputs.decimate_smooth = 0
            #-----------------------------------------------------------------
            # Compute Zernike moments of sulci:
            #-----------------------------------------------------------------
            if do_sulci:
                ZernikeSulci = ZernikeLabels.clone('Zernike_sulci')
                SurfFeatureShapeFlow.add_nodes([ZernikeSulci])
                mbFlow.connect(SurfFeatureFlow, 'Sulci.sulci_file',
                               SurfFeatureShapeFlow, 'Zernike_sulci.vtk_file')
                ZernikeSulci.inputs.exclude_labels = [-1]
    
    #=========================================================================
    #
    #   Surface feature shape tables
    #
    #=========================================================================
    if do_shapes:
        #---------------------------------------------------------------------
        # Surface feature shape tables: labels, sulci, fundi:
        #---------------------------------------------------------------------
        ShapeTables = Node(name='Shape_tables',
                           interface=Fn(function=write_shape_stats,
                                input_names=['labels_or_file',
                                             'sulci',
                                             'fundi',
                                             'affine_transform_file',
                                             'transform_format',
                                             'area_file',
                                             'mean_curvature_file',
                                             'travel_depth_file',
                                             'geodesic_depth_file',
                                             'freesurfer_convexity_file',
                                             'freesurfer_thickness_file',
                                             'labels_spectra',
                                             'labels_spectra_IDs',
                                             'sulci_spectra',
                                             'sulci_spectra_IDs',
                                             'labels_zernike',
                                             'labels_zernike_IDs',
                                             'sulci_zernike',
                                             'sulci_zernike_IDs',
                                             'exclude_labels',
                                             'delimiter'],
                                output_names=['label_table',
                                              'sulcus_table',
                                              'fundus_table']))
        mbFlow.add_nodes([ShapeTables])
        if do_label:
            mbFlow.connect(SurfLabelFlow, 'Reindex_labels.output_file',
                           ShapeTables, 'labels_or_file')
        else:
            ShapeTables.inputs.labels_or_file = []
        if do_sulci:
            mbFlow.connect(SurfFeatureFlow, 'Sulci.sulci',
                           ShapeTables, 'sulci')
        else:
            ShapeTables.inputs.sulci = []
        if do_fundi:
            mbFlow.connect(SurfFeatureFlow,
                           'Fundus_per_sulcus.fundus_per_sulcus',
                           ShapeTables, 'fundi')
        else:
            ShapeTables.inputs.fundi = []
    
        if do_ants:
            mbFlow.connect(ComposeAffine, 'output_transform_file',
                           ShapeTables, 'affine_transform_file')
            ShapeTables.inputs.transform_format = 'itk'
        else:
            ShapeTables.inputs.affine_transform_file = None
            ShapeTables.inputs.transform_format = None
    
        mbFlow.connect([(WholeSurfShapeFlow, ShapeTables,
                           [('Surface_area.area_file',
                             'area_file'),
                            ('Curvature.mean_curvature_file',
                             'mean_curvature_file'),
                            ('Travel_depth.depth_file',
                             'travel_depth_file'),
                            ('Geodesic_depth.depth_file',
                             'geodesic_depth_file')])])
        if do_freesurfer_convexity:
            mbFlow.connect(WholeSurfShapeFlow, 'Convexity_to_vtk.output_vtk',
                           ShapeTables, 'freesurfer_convexity_file')
        else:
            ShapeTables.inputs.freesurfer_convexity_file = ''
        if do_freesurfer_thickness:
            mbFlow.connect(WholeSurfShapeFlow, 'Thickness_to_vtk.output_vtk',
                           ShapeTables, 'freesurfer_thickness_file')
        else:
            ShapeTables.inputs.freesurfer_thickness_file = ''
    
        # Laplace-Beltrami spectra:
        if do_spectra:
            mbFlow.connect(SurfFeatureShapeFlow,
                           'Spectra_labels.spectrum_lists',
                           ShapeTables, 'labels_spectra')
            mbFlow.connect(SurfFeatureShapeFlow, 'Spectra_labels.label_list',
                           ShapeTables, 'labels_spectra_IDs')
            if do_sulci:
                mbFlow.connect(SurfFeatureShapeFlow,
                               'Spectra_sulci.spectrum_lists',
                               ShapeTables, 'sulci_spectra')
                mbFlow.connect(SurfFeatureShapeFlow,
                               'Spectra_sulci.label_list',
                               ShapeTables, 'sulci_spectra_IDs')
            else:
                ShapeTables.inputs.sulci_spectra = []
                ShapeTables.inputs.sulci_spectra_IDs = []
        else:
            ShapeTables.inputs.labels_spectra = []
            ShapeTables.inputs.sulci_spectra = []
            ShapeTables.inputs.labels_spectra_IDs = []
            ShapeTables.inputs.sulci_spectra_IDs = []
    
        # Zernike moments:
        if do_zernike:
            mbFlow.connect(SurfFeatureShapeFlow,
                           'Zernike_labels.descriptors_lists',
                           ShapeTables, 'labels_zernike')
            mbFlow.connect(SurfFeatureShapeFlow, 'Zernike_labels.label_list',
                           ShapeTables, 'labels_zernike_IDs')
            if do_sulci:
                mbFlow.connect(SurfFeatureShapeFlow,
                               'Zernike_sulci.descriptors_lists',
                               ShapeTables, 'sulci_zernike')
                mbFlow.connect(SurfFeatureShapeFlow,
                               'Zernike_sulci.label_list',
                               ShapeTables, 'sulci_zernike_IDs')
            else:
                ShapeTables.inputs.sulci_zernike = []
                ShapeTables.inputs.sulci_zernike_IDs = []
        else:
            ShapeTables.inputs.labels_zernike = []
            ShapeTables.inputs.sulci_zernike = []
            ShapeTables.inputs.labels_zernike_IDs = []
            ShapeTables.inputs.sulci_zernike_IDs = []
    
        ShapeTables.inputs.exclude_labels = [-1]
        ShapeTables.inputs.delimiter = ","
        mbFlow.connect(ShapeTables, 'label_table', Sink, 'tables.@labels')
        if do_sulci:
            mbFlow.connect(ShapeTables, 'sulcus_table', Sink, 'tables.@sulci')
        if do_fundi:
            mbFlow.connect(ShapeTables, 'fundus_table', Sink, 'tables.@fundi')
        #---------------------------------------------------------------------
        # Vertex measures table:
        #---------------------------------------------------------------------
        if args.vertices:
            VertexTable = Node(name='Vertex_table',
                               interface=Fn(function=write_vertex_measures,
                                    input_names=['output_table',
                                                 'labels_or_file',
                                                 'sulci',
                                                 'fundi',
                                                 'affine_transform_file',
                                                 'transform_format',
                                                 'area_file',
                                                 'mean_curvature_file',
                                                 'travel_depth_file',
                                                 'geodesic_depth_file',
                                                 'freesurfer_convexity_file',
                                                 'freesurfer_thickness_file',
                                                 'delimiter'],
                                    output_names=['output_table']))
            mbFlow.add_nodes([VertexTable])
            VertexTable.inputs.output_table = ''
            if do_label:
                mbFlow.connect(SurfLabelFlow, 'Reindex_labels.output_file',
                               VertexTable, 'labels_or_file')
            else:
                VertexTable.inputs.labels_or_file = []
            if do_sulci:
                mbFlow.connect(SurfFeatureFlow, 'Sulci.sulci',
                               VertexTable, 'sulci')
            else:
                VertexTable.inputs.sulci = []
            if do_fundi:
                mbFlow.connect(SurfFeatureFlow,
                               'Fundus_per_sulcus.fundus_per_sulcus',
                               VertexTable, 'fundi')
            else:
                VertexTable.inputs.fundi = []
    
            if do_ants:
                mbFlow.connect(ComposeAffine, 'output_transform_file',
                               VertexTable, 'affine_transform_file')
                VertexTable.inputs.transform_format = 'itk'
            else:
                VertexTable.inputs.affine_transform_file = None
                VertexTable.inputs.transform_format = None
    
            mbFlow.connect([(WholeSurfShapeFlow, VertexTable,
                               [('Surface_area.area_file','area_file'),
                                ('Travel_depth.depth_file',
                                 'travel_depth_file'),
                                ('Geodesic_depth.depth_file',
                                 'geodesic_depth_file'),
                                ('Curvature.mean_curvature_file',
                                 'mean_curvature_file')])])
            if do_freesurfer_thickness:
                mbFlow.connect(WholeSurfShapeFlow,
                               'Thickness_to_vtk.output_vtk',
                               VertexTable, 'freesurfer_thickness_file')
            else:
                VertexTable.inputs.freesurfer_thickness_file = ''
            if do_freesurfer_convexity:
                mbFlow.connect(WholeSurfShapeFlow,
                               'Convexity_to_vtk.output_vtk',
                               VertexTable, 'freesurfer_convexity_file')
            else:
                VertexTable.inputs.freesurfer_convexity_file = ''
    
            VertexTable.inputs.delimiter = ","
            mbFlow.connect(VertexTable, 'output_table',
                           Sink, 'tables.@vertices')
    
        # #---------------------------------------------------------------------
        # # Apply RegFlows's affine transform to surface coordinates:
        # #---------------------------------------------------------------------
        # TransformPoints = Node(name='Transform_surface_points',
        #                        interface=Fn(function=apply_affine_transform,
        #                                     input_names=['transform_file',
        #                                                  'vtk_or_points',
        #                                                  'transform_format',
        #                                                  'save_file'],
        #                                     output_names=['affine_points',
        #                                                   'output_file']))
        # VolLabelFlow.add_nodes([TransformPoints])
        # if do_ants:
        #     mbFlow.connect(ComposeAffine, 'output_transform_file',
        #                   TransformPoints, 'transform_file')
        # SurfShapeFlow.connect(TravelDepth, 'depth_file',
        #                       TransformPoints, 'vtk_or_points')
        # TransformPoints.inputs.save_file = True
        # mbFlow.connect(SurfShapeFlow, 'Transform_surface_points.output_file',
        #                Sink, 'transforms.@points_to_template')


#=============================================================================
#-----------------------------------------------------------------------------
#
#   Volume workflows
#
#-----------------------------------------------------------------------------
#=============================================================================
if do_volumes and do_label:

    #=========================================================================
    #
    #   Location and structure of FreeSurfer volume inputs
    #
    #=========================================================================
    #-------------------------------------------------------------------------
    # Use independently generated label volumes in nifti format:
    #-------------------------------------------------------------------------
    if do_input_fs_labels:
        asegNifti = Node(name='aseg_nifti',
                         interface=DataGrabber(infields=['subject'],
                                               outfields=['aseg'],
                                               sort_filelist=False))
        asegNifti.inputs.base_directory = freesurfer_data
        asegNifti.inputs.template = '%s/mri/aseg.nii.gz'
        asegNifti.inputs.template_args['aseg'] = [['subject']]
        mbFlow.connect(InputSubjects, 'subject', asegNifti, 'subject')

        filledNifti = Node(name='filled_nifti',
                           interface=DataGrabber(infields=['subject'],
                                                 outfields=['filled'],
                                                 sort_filelist=False))
        filledNifti.inputs.base_directory = freesurfer_data
        filledNifti.inputs.template = '%s/mri/filled.nii.gz'
        filledNifti.inputs.template_args['filled'] = [['subject']]
        mbFlow.connect(InputSubjects, 'subject', filledNifti, 'subject')
    #-------------------------------------------------------------------------
    # Convert FreeSurfer label volumes to nifti format:
    #-------------------------------------------------------------------------
    else:
        # Original image (.mgz) for converting from conformal (below):
        mghOrig = Node(name='mgh_orig',
                       interface=DataGrabber(infields=['subject'],
                                             outfields=['mgh_orig'],
                                             sort_filelist=False))
        mghOrig.inputs.base_directory = freesurfer_data
        mghOrig.inputs.template = '%s/mri/orig/001.mgz'
        mghOrig.inputs.template_args['mgh_orig'] = [['subject']]
        mbFlow.connect(InputSubjects, 'subject', mghOrig, 'subject')

        # aseg label volume:
        asegMGH = Node(name='aseg_mgh',
                       interface=DataGrabber(infields=['subject'],
                                             outfields=['aseg'],
                                             sort_filelist=False))
        asegMGH.inputs.base_directory = freesurfer_data
        asegMGH.inputs.template = '%s/mri/aseg.mgz'
        asegMGH.inputs.template_args['aseg'] = [['subject']]
        mbFlow.connect(InputSubjects, 'subject', asegMGH, 'subject')

        # Convert FreeSurfer mgh conformal file to nifti format:
        asegMGH2Nifti = Node(name='aseg_mgh_to_nifti',
                             interface=MRIConvert())
        mbFlow.add_nodes([asegMGH2Nifti])
        mbFlow.connect(asegMGH, 'aseg', asegMGH2Nifti, 'in_file')
        mbFlow.connect(mghOrig, 'mgh_orig', asegMGH2Nifti, 'reslice_like')
        asegMGH2Nifti.inputs.resample_type = 'nearest'
        asegMGH2Nifti.inputs.out_type = 'niigz'
        asegMGH2Nifti.inputs.out_file = 'aseg.nii.gz'
        #mbFlow.connect(asegMGH2Nifti, 'out_file', Sink, 'brain.@aseg')

        filledMGH = Node(name='filled_mgh',
                         interface=DataGrabber(infields=['subject'],
                                               outfields=['filled'],
                                               sort_filelist=False))
        filledMGH.inputs.base_directory = freesurfer_data
        filledMGH.inputs.template = '%s/mri/filled.mgz'
        filledMGH.inputs.template_args['filled'] = [['subject']]
        mbFlow.connect(InputSubjects, 'subject', filledMGH, 'subject')

        # Convert FreeSurfer mgh conformal file to nifti format:
        filledMGH2Nifti = Node(name='filled_mgh_to_nifti',
                               interface=MRIConvert())
        mbFlow.add_nodes([filledMGH2Nifti])
        mbFlow.connect(filledMGH, 'filled', filledMGH2Nifti, 'in_file')
        mbFlow.connect(mghOrig, 'mgh_orig', filledMGH2Nifti, 'reslice_like')
        filledMGH2Nifti.inputs.resample_type = 'nearest'
        filledMGH2Nifti.inputs.out_type = 'niigz'
        filledMGH2Nifti.inputs.out_file = 'filled.nii.gz'
        #mbFlow.connect(filledMGH2Nifti, 'out_file', Sink, 'brain.@filled')

    #=========================================================================
    #
    #   Volume labels
    #
    #=========================================================================
    VolLabelFlow = Workflow(name='Volume_labels')

    #=========================================================================
    # Combine segmentation volumes to obtain a single file per hemisphere
    #=========================================================================
    #-------------------------------------------------------------------------    
    # Combine FreeSurfer and ANTs (if do_ants) segmentation volumes
    # to obtain a single cortex (2) and noncortex (3) segmentation file:
    #-------------------------------------------------------------------------    
    JoinSegs = Node(name='Combine_FreeSurfer_ANTs_segmentations',
                    interface=Fn(function=combine_segmentations,
                                 input_names=['subject',
                                              'aseg',
                                              'filled',
                                              'out_dir',
                                              'second_segmentation_file',
                                              'cortex_value',
                                              'noncortex_value',
                                              'use_c3d'],
                                 output_names=['segmented_file']))
    VolLabelFlow.add_nodes([JoinSegs])
    if do_input_fs_labels:
        mbFlow.connect(asegNifti, 'aseg', VolLabelFlow, 
                       'Combine_FreeSurfer_ANTs_segmentations.aseg')
        mbFlow.connect(filledNifti, 'filled', 'out_file', VolLabelFlow, 
                       'Combine_FreeSurfer_ANTs_segmentations.filled')
    else:
        mbFlow.connect(asegMGH2Nifti, 'out_file', VolLabelFlow, 
                       'Combine_FreeSurfer_ANTs_segmentations.aseg')
        mbFlow.connect(filledMGH2Nifti, 'out_file', VolLabelFlow, 
                       'Combine_FreeSurfer_ANTs_segmentations.filled')
    JoinSegs.inputs.out_dir = ''
    if do_ants:
        mbFlow.connect(FetchANTs, 'segments', VolLabelFlow,
            'Combine_FreeSurfer_ANTs_segmentations.second_segmentation_file')
    else:
        JoinSegs.inputs.second_segmentation_file = ''
    JoinSegs.inputs.cortex_value = 2
    JoinSegs.inputs.noncortex_value = 3
    JoinSegs.inputs.use_c3d = False
    mbFlow.connect(VolLabelFlow,
       'Combine_FreeSurfer_ANTs_segmentations.segmented_file',
       Sink, 'labels.@cortex_and_noncortex_file')
    #-------------------------------------------------------------------------
    # Remove medial labels so as to fill brain with left or right labels:
    #-------------------------------------------------------------------------
    RemoveMedial = Node(name='Remove_medial_labels',
                        interface=Fn(function=keep_volume_labels,
                                     input_names=['input_file',
                                                  'labels_to_keep',
                                                  'output_file'],
                                     output_names=['output_file']))
    VolLabelFlow.add_nodes([RemoveMedial])
    if do_input_fs_labels:
        mbFlow.connect(asegNifti, 'aseg', VolLabelFlow,
                       'Remove_medial_labels.input_file')
    else:
        mbFlow.connect(asegMGH2Nifti, 'out_file', VolLabelFlow,
                       'Remove_medial_labels.input_file')
    RemoveMedial.inputs.labels_to_keep = left_label_numbers + \
                                         right_label_numbers + [2, 3, 41, 42]
    RemoveMedial.inputs.output_file = ''
    #-------------------------------------------------------------------------
    # Propagate nonmedial FreeSurfer labels through brain:
    #-------------------------------------------------------------------------    
    FillBrain = Node(name='Fill_brain_with_left_right_labels',
                     interface=Fn(function=PropagateLabelsThroughMask,
                                  input_names=['mask',
                                               'labels',
                                               'mask_index',
                                               'output_file',
                                               'binarize',
                                               'stopvalue'],
                                  output_names=['output_file']))
    VolLabelFlow.add_nodes([FillBrain])
    VolLabelFlow.connect(JoinSegs, 'segmented_file', FillBrain, 'mask')
    VolLabelFlow.connect(RemoveMedial, 'output_file', FillBrain, 'labels')
    FillBrain.inputs.mask_index = ''
    FillBrain.inputs.output_file = ''
    FillBrain.inputs.binarize = True
    FillBrain.inputs.stopvalue = ''
    #-------------------------------------------------------------------------
    # Split brain by masking with left or right labels:
    #-------------------------------------------------------------------------    
    SplitBrain = Node(name='Split_brain',
                      interface=Fn(function=keep_volume_labels,
                                   input_names=['input_file',
                                                'labels_to_keep',
                                                'output_file'],
                                   output_names=['output_file']))
    VolLabelFlow.add_nodes([SplitBrain])
    VolLabelFlow.connect(FillBrain, 'output_file', SplitBrain, 'input_file')
    SplitBrain.iterables = ('labels_to_keep', [left_label_numbers + [2, 3],
                                              right_label_numbers + [41, 42]])
    SplitBrain.inputs.output_file = ''
    #-------------------------------------------------------------------------
    # Create a mask for the left or right labels:
    #-------------------------------------------------------------------------
    CreateHemiMask = Node(name='Create_half_brain_mask',
                          interface=Fn(function=ThresholdImage,
                                       input_names=['volume',
                                                    'output_file',
                                                    'threshlo',
                                                    'threshhi'],
                                       output_names=['output_file']))
    VolLabelFlow.add_nodes([CreateHemiMask])
    VolLabelFlow.connect(SplitBrain, 'output_file', CreateHemiMask, 'volume')
    CreateHemiMask.inputs.output_file = ''
    CreateHemiMask.inputs.threshlo = 1
    CreateHemiMask.inputs.threshhi = 10000
    #-------------------------------------------------------------------------
    # Split segmented brain by masking with left or right labels:
    #-------------------------------------------------------------------------
    MaskHemi = Node(name='Mask_half_segmented_brain',
                    interface=Fn(function=ImageMath,
                                 input_names=['volume1',
                                              'volume2',
                                              'operator',
                                              'output_file'],
                                 output_names=['output_file']))
    VolLabelFlow.add_nodes([MaskHemi])
    VolLabelFlow.connect(JoinSegs, 'segmented_file', MaskHemi, 'volume1')
    VolLabelFlow.connect(CreateHemiMask, 'output_file', MaskHemi, 'volume2')
    MaskHemi.inputs.operator = 'm'
    MaskHemi.inputs.output_file = ''

    #=========================================================================
    # Fill segmentation volumes with ANTs labels
    #=========================================================================
    if do_ants:

        #---------------------------------------------------------------------
        # Transform atlas labels in MNI152 to subject via template:
        #---------------------------------------------------------------------
        xfm = Node(ApplyTransforms(), name='antsApplyTransforms')
        VolLabelFlow.add_nodes([xfm])
        xfm.inputs.dimension = 3
        xfm.inputs.default_value = 0
        xfm.inputs.output_image = 'volume_registered_labels.nii.gz'
        xfm.inputs.interpolation = 'NearestNeighbor'
        xfm.inputs.invert_transform_flags = warp_inverse_Booleans
        mbFlow.connect(FetchANTs, 'brain', VolLabelFlow,
                       'antsApplyTransforms.reference_image')
        mbFlow.connect(FetchAtlas, 'data_path',
                       VolLabelFlow, 'antsApplyTransforms.input_image')
        mbFlow.connect(WarpToSubjectFileList, 'string_list', VolLabelFlow,
                       'antsApplyTransforms.transforms')
        mbFlow.connect(VolLabelFlow, 'antsApplyTransforms.output_image',
                       Sink, 'labels.@antsRegistration')
        #---------------------------------------------------------------------
        # Remove noncortical ANTs volume labels:
        #---------------------------------------------------------------------
        noANTSwm = Node(name='Remove_noncortex_ANTs_labels',
                        interface=Fn(function=keep_volume_labels,
                                     input_names=['input_file',
                                                  'labels_to_keep',
                                                  'output_file'],
                                     output_names=['output_file']))
        VolLabelFlow.add_nodes([noANTSwm])
        VolLabelFlow.connect(xfm, 'output_image', noANTSwm, 'input_file')
        noANTSwm.inputs.labels_to_keep = cortex_numbers
        noANTSwm.inputs.output_file = ''
        #---------------------------------------------------------------------
        # Remove cortical ANTs volume labels:
        #---------------------------------------------------------------------
        noANTSgm = noANTSwm.clone('Remove_cortex_ANTs_labels')
        VolLabelFlow.add_nodes([noANTSgm])
        VolLabelFlow.connect(xfm, 'output_image', noANTSgm, 'input_file')
        noANTSgm.inputs.labels_to_keep = noncortex_numbers
        noANTSgm.inputs.output_file = ''
        #---------------------------------------------------------------------
        # Propagate ANTs cortical volume labels through cortex=2:
        #---------------------------------------------------------------------
        ants2gray = Node(name='Fill_cortex_with_ANTs_labels',
                         interface=Fn(function=PropagateLabelsThroughMask,
                                      input_names=['mask',
                                                   'labels',
                                                   'mask_index',
                                                   'output_file',
                                                   'binarize',
                                                   'stopvalue'],
                                      output_names=['output_file']))
        VolLabelFlow.add_nodes([ants2gray])
        VolLabelFlow.connect(MaskHemi, 'output_file', ants2gray, 'mask')
        VolLabelFlow.connect(noANTSwm, 'output_file', ants2gray, 'labels')
        ants2gray.inputs.mask_index = 2
        ants2gray.inputs.output_file = ''
        ants2gray.inputs.binarize = False
        ants2gray.inputs.stopvalue = ''
        #---------------------------------------------------------------------
        # Propagate ANTs volume labels through noncortex=3:
        #---------------------------------------------------------------------
        ants2white = ants2gray.clone('Fill_noncortex_with_ANTs_labels')
        VolLabelFlow.add_nodes([ants2white])
        VolLabelFlow.connect(MaskHemi, 'output_file', ants2white, 'mask')
        VolLabelFlow.connect(noANTSgm, 'output_file', ants2white, 'labels')
        ants2white.inputs.mask_index = 3
        #---------------------------------------------------------------------
        # Combine ANTs label-filled cortex and label-filled noncortex:
        #---------------------------------------------------------------------
        antslabels = Node(name='ANTs_filled_labels',
                          interface=Fn(function=overwrite_volume_labels,
                                       input_names=['source',
                                                    'target',
                                                    'output_file',
                                                    'ignore_labels',
                                                    'replace'],
                                       output_names=['output_file']))
        VolLabelFlow.add_nodes([antslabels])
        VolLabelFlow.connect(ants2white, 'output_file',
                             antslabels, 'source')
        VolLabelFlow.connect(ants2gray, 'output_file',
                             antslabels, 'target')
        antslabels.inputs.output_file = ''
        antslabels.inputs.ignore_labels = [0]
        antslabels.inputs.replace = True
        mbFlow.connect(VolLabelFlow, 
                       'ANTs_filled_labels.output_file',
                       Sink, 'labels.@ants_filled')

    #=========================================================================
    # Fill segmentation volumes with FreeSurfer labels
    #=========================================================================
    if do_surface:
        #---------------------------------------------------------------------
        # Propagate FreeSurfer surface labels through cortex:
        #---------------------------------------------------------------------
        FS2gray = Node(name='Fill_cortex_with_FreeSurfer_labels',
                       interface=Fn(function=fill_volume_with_surface_labels,
                                    input_names=['mask',
                                                 'surface_files',
                                                 'mask_index',
                                                 'output_file',
                                                 'binarize'],
                                    output_names=['output_file']))
        VolLabelFlow.add_nodes([FS2gray])
        FS2gray.inputs.mask_index = 2
        VolLabelFlow.connect(JoinSegs, 'segmented_file', FS2gray, 'mask')
        mbFlow.connect(SurfLabelFlow, 'Reindex_labels.output_file',
                       VolLabelFlow,
                       'Fill_cortex_with_FreeSurfer_labels.surface_files')
        FS2gray.inputs.output_file = ''
        FS2gray.inputs.binarize = False
        #---------------------------------------------------------------------
        # Propagate FreeSurfer volume labels through noncortex:
        #---------------------------------------------------------------------
        # Remove cortical FreeSurfer volume labels:
        noFSgm = Node(name='Remove_cortex_FreeSurfer_labels',
                      interface=Fn(function=keep_volume_labels,
                                   input_names=['input_file',
                                                'labels_to_keep',
                                                'output_file'],
                                   output_names=['output_file']))
        VolLabelFlow.add_nodes([noFSgm])
        if do_input_fs_labels:
            mbFlow.connect(asegNifti, 'aseg', VolLabelFlow,
                           'Remove_cortex_FreeSurfer_labels.input_file')
        else:
            mbFlow.connect(asegMGH2Nifti, 'out_file', VolLabelFlow,
                           'Remove_cortex_FreeSurfer_labels.input_file')
        noFSgm.inputs.labels_to_keep = noncortex_numbers + [2, 41]
        noFSgm.inputs.output_file = ''

        FS2white = Node(name='Fill_noncortex_with_FreeSurfer_labels',
                        interface=Fn(function=PropagateLabelsThroughMask,
                                     input_names=['mask',
                                                  'labels',
                                                  'mask_index',
                                                  'output_file',
                                                  'binarize',
                                                  'stopvalue'],
                                     output_names=['output_file']))
        VolLabelFlow.add_nodes([FS2white])
        VolLabelFlow.connect(JoinSegs, 'segmented_file', FS2white, 'mask')
        VolLabelFlow.connect(noFSgm, 'output_file', FS2white, 'labels')
        FS2white.inputs.mask_index = 3
        FS2white.inputs.output_file = ''
        FS2white.inputs.binarize = False
        FS2white.inputs.stopvalue = ''
        #---------------------------------------------------------------------
        # Combine FreeSurfer label-filled cortex and noncortex:
        #---------------------------------------------------------------------
        FSlabels = Node(name='FreeSurfer_filled_labels',
                        interface=Fn(function=overwrite_volume_labels,
                                     input_names=['source',
                                                  'target',
                                                  'output_file',
                                                  'ignore_labels',
                                                  'replace'],
                                     output_names=['output_file']))
        VolLabelFlow.add_nodes([FSlabels])
        VolLabelFlow.connect(FS2white, 'output_file', FSlabels, 'source')
        VolLabelFlow.connect(FS2gray, 'output_file', FSlabels, 'target')
        FSlabels.inputs.output_file = ''
        FSlabels.inputs.ignore_labels = [0]
        FSlabels.inputs.replace = True
        mbFlow.connect(VolLabelFlow, 
                   'FreeSurfer_filled_labels.output_file',
                   Sink, 'labels.@freesurfer_filled')

    #=========================================================================
    # Combine FreeSurfer and ANTs cortical and noncortical labels
    #=========================================================================
    if antsurfer_labels and do_ants:

        #---------------------------------------------------------------------
        # ANTs label-filled noncortex and FreeSurfer label-filled cortex:
        #---------------------------------------------------------------------
        ANTSwFSg = antslabels.\
            clone('FreeSurfer_cortex_ANTs_noncortex_labels')
        VolLabelFlow.add_nodes([ANTSwFSg])
        VolLabelFlow.connect(ants2white, 'output_file',
                             ANTSwFSg, 'source')
        VolLabelFlow.connect(FS2gray, 'output_file', ANTSwFSg, 'target')
        mbFlow.connect(VolLabelFlow,
               'FreeSurfer_cortex_ANTs_noncortex_labels.output_file',
               Sink, 'labels.@fs_cortex_ants_noncortex')
        #---------------------------------------------------------------------
        # FreeSurfer label-filled noncortex and ANTs label-filled cortex:
        #---------------------------------------------------------------------
        FSwANTSg = antslabels.\
            clone('ANTs_cortex_FreeSurfer_noncortex_labels')
        VolLabelFlow.add_nodes([FSwANTSg])
        VolLabelFlow.connect(FS2white, 'output_file', FSwANTSg, 'source')
        VolLabelFlow.connect(ants2gray, 'output_file', FSwANTSg, 'target')
        mbFlow.connect(VolLabelFlow,
               'ANTs_cortex_FreeSurfer_noncortex_labels.output_file',
               Sink, 'labels.@ants_cortex_fs_noncortex')

    ##=========================================================================
    ## Evaluate label volume overlaps
    ##=========================================================================
    #if do_evaluate_vol_labels:
    #
    #    #---------------------------------------------------------------------
    #    # Evaluation inputs: location and structure of atlas volumes
    #    #---------------------------------------------------------------------
    #    VolAtlas = Node(name='Volume_atlas',
    #                    interface=DataGrabber(infields=['subject'],
    #                                          outfields=['atlas_vol_file'],
    #                                          sort_filelist=False))
    #    VolLabelFlow.add_nodes([VolAtlas])
    #    VolAtlas.inputs.base_directory = freesurfer_data
    #    VolAtlas.inputs.template = '%s/mri/labels.DKT31.manual.nii.gz'
    #    VolAtlas.inputs.template_args['atlas_vol_file'] = [['subject']]
    #    mbFlow.connect(InputSubjects, 'subject',
    #                   VolLabelFlow, 'Volume_atlas.subject')
    #    #---------------------------------------------------------------------
    #    # Evaluate volume labels
    #    #---------------------------------------------------------------------
    #    EvalVolLabels = Node(name='Evaluate_volume_labels',
    #                         interface=Fn(function=measure_volume_overlap,
    #                                      input_names=['labels',
    #                                                   'file2',
    #                                                   'file1'],
    #                                      output_names=['overlaps',
    #                                                    'out_file']))
    #    VolLabelFlow.add_nodes([EvalVolLabels])
    #    EvalVolLabels.inputs.labels = label_numbers
    #    VolLabelFlow.connect(VolAtlas, 'atlas_vol_file',
    #                         EvalVolLabels, 'file2')
    #    if do_ants:
    #        VolLabelFlow.connect(ANTSwFSg, 'output_file',
    #                             EvalVolLabels, 'file1')
    #    else:
    #        VolLabelFlow.connect(FSlabels, 'output_file',
    #                             EvalVolLabels, 'file1')

    #=========================================================================
    #
    #   Volume feature shapes
    #
    #=========================================================================
    if do_shapes:

        VolShapeFlow = Workflow(name='Volume_feature_shapes')
    
        FSVolTable = Node(name='FreeSurfer_filled_label_volume_table',
                          interface=Fn(function=write_columns,
                                       input_names=['columns',
                                                    'column_names',
                                                    'delimiter',
                                                    'quote',
                                                    'input_table',
                                                    'output_table'],
                                       output_names=['output_table']))
        VolShapeFlow.add_nodes([FSVolTable])
        FSVolTable.inputs.column_names = ['label', 'volume']
        FSVolTable.inputs.delimiter = ','
        FSVolTable.inputs.quote = True
        FSVolTable.inputs.input_table = ''
    
        #=====================================================================
        # Measure volume of each region of a labeled image file
        #=====================================================================
        #---------------------------------------------------------------------
        # Volumes of the FreeSurfer filled labels:
        #---------------------------------------------------------------------
        FSlabelVolumes = Node(name='FreeSurfer_filled_label_volumes',
                              interface=Fn(function=volume_per_label,
                                           input_names=['labels',
                                                        'input_file'],
                                           output_names=['labels_volumes']))
        VolShapeFlow.add_nodes([FSlabelVolumes])
        FSlabelVolumes.inputs.labels = label_numbers
        mbFlow.connect(VolLabelFlow, 'FreeSurfer_filled_labels.output_file',
                       VolShapeFlow,
                       'FreeSurfer_filled_label_volumes.input_file')
        # Table:
        VolShapeFlow.connect(FSlabelVolumes, 'labels_volumes',
                             FSVolTable, 'columns')
        s = 'volumes_of_FreeSurfer_labels.csv'
        FSVolTable.inputs.output_table = s
        mbFlow.connect(VolShapeFlow,
                       'FreeSurfer_filled_label_volume_table.output_table',
                       Sink, 'tables.@freesurfer_label_volumes')
        if do_ants:
            #-----------------------------------------------------------------
            # Volumes of the ANTs filled labels:
            #-----------------------------------------------------------------
            antsLabelVolumes = FSlabelVolumes.clone(
                'ANTs_filled_label_volumes')
            VolShapeFlow.add_nodes([antsLabelVolumes])
            mbFlow.connect(VolLabelFlow, 'ANTs_filled_labels.output_file',
                           VolShapeFlow,
                           'ANTs_filled_label_volumes.input_file')
            # Table:
            antsVolTable = FSVolTable.clone('ANTs_filled_label_volume_table')
            VolShapeFlow.add_nodes([antsVolTable])
            VolShapeFlow.connect(antsLabelVolumes, 'labels_volumes',
                                 antsVolTable, 'columns')
            s = 'volumes_of_ANTs_labels.csv'
            antsVolTable.inputs.output_table = s
            mbFlow.connect(VolShapeFlow,
                           'ANTs_filled_label_volume_table.output_table',
                           Sink, 'tables.@ants_label_volumes')
            if antsurfer_labels:
                #-------------------------------------------------------------
                # Volumes of the FreeSurfer cortex + ANTS noncortex labels:
                #-------------------------------------------------------------
                FSgmANTSwmLabelVolumes = FSlabelVolumes.\
                    clone('FreeSurfer_cortex_ANTs_noncortex_label_volumes')
                VolShapeFlow.add_nodes([FSgmANTSwmLabelVolumes])
                mbFlow.connect(VolLabelFlow,
                  'FreeSurfer_cortex_ANTs_noncortex_labels.output_file',
                  VolShapeFlow,
                  'FreeSurfer_cortex_ANTs_noncortex_label_volumes.input_file')
                # Table:
                FSgmANTSwmVolTable = FSVolTable.\
                  clone('FreeSurfer_cortex_ANTs_noncortex_label_volume_table')
                VolShapeFlow.add_nodes([FSgmANTSwmVolTable])
                VolShapeFlow.connect(FSgmANTSwmLabelVolumes, 'labels_volumes',
                                     FSgmANTSwmVolTable, 'columns')
                s = 'volumes_of_FreeSurfer_cortex_ANTs_noncortex_labels.csv'
                FSgmANTSwmVolTable.inputs.output_table = s
                mbFlow.connect(VolShapeFlow,
                  'FreeSurfer_cortex_ANTs_noncortex_label_volume_table.'
                  'output_table', Sink,
                  'tables.@FreeSurfer_cortex_ANTs_noncortex_label_volumes')
                #-------------------------------------------------------------
                # Volumes of the FreeSurfer noncortex + ANTS cortex labels:
                #-------------------------------------------------------------
                FSwANTSgLabelVolumes = FSlabelVolumes.\
                    clone('FreeSurfer_noncortex_ANTs_cortex_label_volumes')
                VolShapeFlow.add_nodes([FSwANTSgLabelVolumes])
                mbFlow.connect(VolLabelFlow,
                  'ANTs_cortex_FreeSurfer_noncortex_labels.output_file',
                  VolShapeFlow,
                  'FreeSurfer_noncortex_ANTs_cortex_label_volumes.input_file')
                # Table:
                ANTSgmFSwmVolTable = FSVolTable.\
                  clone('FreeSurfer_noncortex_ANTs_cortex_label_volume_table')
                VolShapeFlow.add_nodes([ANTSgmFSwmVolTable])
                VolShapeFlow.connect(FSwANTSgLabelVolumes, 'labels_volumes',
                                     ANTSgmFSwmVolTable, 'columns')
                s = 'volumes_of_FreeSurfer_noncortex_ANTs_cortex_labels.csv'
                ANTSgmFSwmVolTable.inputs.output_table = s
                mbFlow.connect(VolShapeFlow,
                  'FreeSurfer_noncortex_ANTs_cortex_label_volume_table.'
                  'output_table', Sink,
                  'tables.@FreeSurfer_noncortex_ants_cortex_label_volumes')
    
        #=====================================================================
        # Measure volume, thickness of cortical regions of labeled image file
        #=====================================================================
        if args.thickness:
            #-----------------------------------------------------------------
            # Thicknesses of the FreeSurfer cortical labels:
            #-----------------------------------------------------------------
            FSgmThicknesses = Node(
                            name='FreeSurfer_filled_cortex_label_thicknesses',
                            interface=Fn(function=thickinthehead,
                                         input_names=['segmented_file',
                                                      'labeled_file',
                                                      'cortex_value',
                                                      'noncortex_value',
                                                      'labels',
                                                      'out_dir',
                                                      'resize',
                                                      'propagate',
                                                      'output_table',
                                                      'use_c3d'],
                                         output_names=['labels_thicknesses',
                                                       'thickness_table']))
            VolShapeFlow.add_nodes([FSgmThicknesses])
            VolShapeFlow.connect(JoinSegs, 'segmented_file',
                                 FSgmThicknesses, 'segmented_file')
            mbFlow.connect(VolLabelFlow,
                   'FreeSurfer_filled_labels.output_file',
                   VolShapeFlow,
                   'FreeSurfer_filled_cortex_label_thicknesses.labeled_file')
            FSgmThicknesses.inputs.cortex_value = 2
            FSgmThicknesses.inputs.noncortex_value = 3
            FSgmThicknesses.inputs.labels = cortex_numbers
            FSgmThicknesses.inputs.out_dir = ''
            FSgmThicknesses.inputs.resize = True
            FSgmThicknesses.inputs.propagate = False
            FSgmThicknesses.inputs.output_table = True
            FSgmThicknesses.inputs.use_c3d = False
            mbFlow.connect(VolShapeFlow,
              'FreeSurfer_filled_cortex_label_thicknesses.thickness_table',
              Sink, 'tables.@FreeSurfer_filled_cortex_label_thicknesses')
            #-----------------------------------------------------------------
            # Thicknesses of the ANTS cortical labels:
            #-----------------------------------------------------------------
            if do_ants:
                ANTSgmThicknesses = FSgmThicknesses.\
                    clone('ANTs_filled_cortex_label_thicknesses')
                VolShapeFlow.add_nodes([ANTSgmThicknesses])
                VolShapeFlow.connect(MaskHemi, 'output_file',
                                     ANTSgmThicknesses, 'segmented_file')
                mbFlow.connect(VolLabelFlow,
                       'ANTs_filled_labels.output_file',
                       VolShapeFlow,
                       'ANTs_filled_cortex_label_thicknesses.labeled_file')
                mbFlow.connect(VolShapeFlow,
                  'ANTs_filled_cortex_label_thicknesses.thickness_table',
                  Sink, 'tables.@ANTs_filled_cortex_label_thicknesses')


#=============================================================================
#-----------------------------------------------------------------------------
#
#   Run workflows
#
#-----------------------------------------------------------------------------
#=============================================================================
if __name__ == '__main__':

    #-------------------------------------------------------------------------
    # Generate a visual graph:
    #-------------------------------------------------------------------------
    graph_vis = args.visual
    if graph_vis == 'hier':
        graph_vis = 'hierarchical'
    if graph_vis:
        if graph_vis == 'exec':
            mbFlow.write_graph(graph2use=graph_vis, simple_form=False)
        else:
            mbFlow.write_graph(graph2use=graph_vis)

    #-------------------------------------------------------------------------
    # Run (HTCondor) cluster processes, such as on the Mindboggler cluster:
    #-------------------------------------------------------------------------
    if args.cluster:
        mbFlow.run(plugin='CondorDAGMan')
    #-------------------------------------------------------------------------
    # Run multiple processes or not:
    #-------------------------------------------------------------------------
    else:
        if args.n:
            if args.n > 1:
                mbFlow.run(plugin='MultiProc',
                           plugin_args={'n_procs': args.n})
            else:
                mbFlow.run()
        else:
            mbFlow.run()
        # # Default is to use all processors:
        #else:
        #    mbFlow.run(plugin='MultiProc')
