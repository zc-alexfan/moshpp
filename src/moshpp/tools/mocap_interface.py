# -*- coding: utf-8 -*-
#
# Copyright (C) 2021 Max-Planck-Gesellschaft zur Förderung der Wissenschaften e.V. (MPG),
# acting on behalf of its Max Planck Institute for Intelligent Systems and the
# Max Planck Institute for Biological Cybernetics. All rights reserved.
#
# Max-Planck-Gesellschaft zur Förderung der Wissenschaften e.V. (MPG) is holder of all proprietary rights
# on this computer program. You can only use this computer program if you have closed a license agreement
# with MPG or you get the right to use the computer program from someone who is authorized to grant you that right.
# Any use of the computer program without a valid license is prohibited and liable to prosecution.
# Contact: ps-license@tuebingen.mpg.de
#
# If you use this code in a research publication please cite the following:
#
# @conference{AMASS:ICCV:2019,
#   title = {{AMASS}: Archive of Motion Capture as Surface Shapes},
#   author = {Mahmood, Naureen and Ghorbani, Nima and Troje, Nikolaus F. and Pons-Moll, Gerard and Black, Michael J.},
#   booktitle = {International Conference on Computer Vision},
#   pages = {5442--5451},
#   month = oct,
#   year = {2019},
#   month_numeric = {10}
# }
#
# You can find complementary content at the project website: https://amass.is.tue.mpg.de/
#
# Code Developed by:
# Nima Ghorbani <https://nghorbani.github.io/>
# While at Max-Planck Institute for Intelligent Systems, Tübingen, Germany
#
# 2021.06.18

import pickle
import time
from collections import OrderedDict
from pathlib import Path
from typing import Union, List, Dict

import ezc3d
import numpy as np
from body_visualizer.mesh.psbody_mesh_sphere import points_to_spheres
from body_visualizer.tools.vis_tools import colors
from human_body_prior.tools.rotation_tools import rotate_points_xyz
from psbody.mesh.meshviewer import MeshViewer
from psbody.mesh.sphere import Sphere


def write_mocap_c3d(markers: np.ndarray, labels: list, out_c3d_fname: str, frame_rate: int = 120):
    """
    Here we are c3d package since ezc3d has a bug by writing nan points which causes non-negative residual values for them
    non-negative residuals for nan points means they wont be shown as occluded in a standard mocap tool
    :param markers:
    :param labels:
    :param out_c3d_fname:
    :param frame_rate:
    """
    # todo: add the ability to write at any scale. alternatively make it standard to mm
    assert out_c3d_fname.endswith('.c3d')
    from moshpp.tools import c3d

    writer = c3d.Writer(point_rate=frame_rate)

    markers = markers * 1000

    x = markers[:, :, 0:1]
    y = markers[:, :, 1:2]
    z = markers[:, :, 2:3]
    points = np.concatenate([x, z, -y,
                             np.ones([markers.shape[0], markers.shape[1], 1]),
                             np.zeros([markers.shape[0], markers.shape[1], 1])], axis=-1).astype(np.float)
    for t in range(points.shape[0]):  # for each frame

        zero_mask = ((points[t] == 0).sum(-1) == 4)
        if zero_mask.sum() != 0:
            points[t, zero_mask, 3] = -1
            points[t, zero_mask, :3] = np.nan

        writer.add_frames([(points[t, :], np.array([[]]))])

    with open(out_c3d_fname, 'wb') as h:
        writer.write(h, labels)


def read_mocap(mocap_fname):
    labels = None
    frame_rate = None
    if mocap_fname.endswith('.mat'):
        import scipy.io
        _marker_data = scipy.io.loadmat(mocap_fname)

        markers = None
        expected_marker_data_fields = ['MoCaps', 'Markers']
        for expected_key in expected_marker_data_fields:
            if expected_key in _marker_data.keys():
                markers = _marker_data[expected_key]
        if markers is None:
            raise ValueError(
                f"The .mat file do not have the expected field for marker data! Expected fields are {expected_marker_data_fields}")
        if 'Labels' in _marker_data.keys():
            labels = np.vstack(_marker_data['Labels'][0]).ravel()

    elif mocap_fname.endswith('.pkl'):
        with open(mocap_fname, 'rb') as f:
            _marker_data = pickle.load(f, encoding='latin-1')
        markers = _marker_data['markers']
        if 'required_parameters' in _marker_data.keys():
            frame_rate = _marker_data['required_parameters']['frame_rate']
        elif 'frame_rate' in _marker_data:
            frame_rate = _marker_data['frame_rate']
        labels = _marker_data.get('labels', False)
        if isinstance(labels, np.ndarray):
            labels = labels.tolist()
        # address a bug in bmlmovi
        labels = [f'*{lid}' if isinstance(l, np.ndarray) else l for lid, l in enumerate(labels)]

    elif mocap_fname.endswith('.c3d'):
        _marker_data = ezc3d.c3d(mocap_fname)
        # points_residuals = c['data']['meta_points']['residuals']
        # analog_data = c['data']['analogs']
        markers = _marker_data['data']['points'][:3].transpose(2, 1, 0)
        frame_rate = _marker_data['parameters']['POINT']['RATE']['value'][0]
        labels = _marker_data['parameters']['POINT']['LABELS']['value']
        if len(labels) < markers.shape[1]:
            labels = labels + [f'*{len(labels) + i:d}' for i in range(markers.shape[1] - len(labels))]

    elif mocap_fname.endswith('.npz'):
        _marker_data = np.load(mocap_fname, allow_pickle=True)
        markers = _marker_data['markers']
        if 'frame_rate' in list(_marker_data.keys()):
            frame_rate = _marker_data['frame_rate']
        elif 'required_parameters' in list(_marker_data.keys()):
            if 'frame_rate' in _marker_data['required_parameters']:
                frame_rate = _marker_data['required_parameters']['frame_rate']

        labels = _marker_data.get('labels', None)

    else:
        raise ValueError(f"Error! Could not recognize file format for {mocap_fname}")

    if labels is None:
        labels = [f'*{i}' for i in range(markers.shape[1])]
    elif len(labels) < markers.shape[1]:
        labels = labels + [f'*{i}' for i in range(markers.shape[1]-len(labels))]

    return {'markers': markers, 'labels': labels, 'frame_rate': frame_rate, '_marker_data': _marker_data}


class MocapSession(object):
    """
    A mocap session is defined by a sequence of frames captured in a single Motion Capture Sequence.
    """

    def __init__(self, mocap_fname: Union[str, Path], mocap_unit: str, mocap_rotate: list = None,
                 exclude_markers: List[str] = None,
                 only_markers: List[str] = None, labels_map: dict = None,
                 ignore_stared_labels: bool = True, remove_label_before_colon: bool = True):
        """

        :param mocap_fname:
        :param mocap_unit:
        :param mocap_rotate:
        :param exclude_markers:
        :param only_markers:
        :param labels_map:
        :param ignore_stared_labels:
        :param remove_label_before_colon:
        """

        scale = {'mm': 1000., 'cm': 100., 'm': 1.}[mocap_unit]
        self.mocap_fname = mocap_fname

        mocap_read = read_mocap(mocap_fname)
        self._marker_data = mocap_read['_marker_data']  # this is used for SOMA evaluation to get per frame labels

        labels = [l.decode() if isinstance(l, bytes) else l for l in mocap_read['labels']]
        labels = [l.replace(' ', '') for l in labels]

        labels = np.vstack(labels).ravel()
        if remove_label_before_colon:
            labels = [l.split(':')[-1] for l in labels]
        if labels_map is not None:
            labels = [labels_map.get(l, l) for l in labels]

        if only_markers is not None:
            good_labels_mask = [l in only_markers for l in labels]
        else:
            good_labels_mask = [True for _ in range(len(labels))]

            if ignore_stared_labels:
                good_labels_mask = [good_labels_mask[i] and not labels[i].startswith('*') for i in range(len(labels))]

            if exclude_markers is not None:
                exclude_markers = [] if exclude_markers is None else exclude_markers

                good_labels_mask = [good_labels_mask[i] and labels[i] not in exclude_markers for i in
                                    range(len(labels))]

        self.labels = [l for l, valid in zip(labels, good_labels_mask) if valid]
        markers = mocap_read['markers'][:, good_labels_mask]
        nan_mask = np.logical_not(MocapSession.marker_availability_mask(markers))
        # nan_mask = np.logical_or(np.isnan(markers).sum(-1) != 0, (markers == 0).sum(-1) == 3)
        markers[nan_mask] = 0

        if mocap_rotate is not None:
            markers = rotate_points_xyz(markers, mocap_rotate).reshape(markers.shape)

        self.markers = markers / [scale]

        self.frame_rate = mocap_read.get('frame_rate', 120.)
        self.frame_rate = 120. if self.frame_rate is None else 120.

    def markers_asdict(self) -> List[Dict[str, np.ndarray]]:
        """
        Returns list of dictionaries. Each dictionary contains the labels & markers for consecutive frames of the capture session in the form:
        --> [{
        label1 : marker 3d coordinates,
        label2 : marker 3d coordinates,
        ...
        }, ....]
        """
        nonan_mask = MocapSession.marker_availability_mask(self.markers)
        label_marker_dict = []
        for t in range(self.markers.shape[0]):
            m = OrderedDict()
            for idx, label in enumerate(self.labels):
                # print(idx, label, nonan_mask[t, idx])
                if nonan_mask[t, idx]:
                    m[label] = self.markers[t, idx, :]

            label_marker_dict.append(m)
        return label_marker_dict

    @staticmethod
    def marker_availability_mask(markers):
        nonan_mask = np.logical_and(np.isnan(markers).sum(-1) == 0, (markers == 0).sum(-1) != 3)

        return nonan_mask

    def __len__(self):
        return self.markers.shape[0]

    def __getitem__(self, given):
        if isinstance(given, slice):
            return self.markers[given.start:given.stop:given.step]
        else:
            return self.markers[given]

    def time_length(self):
        """
        :return: time length in seconds
        """
        assert self.frame_rate is not None, ValueError(f'mocap frame_rate is unknown: {self.mocap_fname}')
        return self.markers.shape[0] / self.frame_rate

    def write_as_c3d(self, out_c3d_fname: Union[str, Path]):
        write_mocap_c3d(markers=self.markers, labels=self.labels,
                        frame_rate=self.frame_rate, out_c3d_fname=out_c3d_fname)

    def write_as_npz(self, out_npz_fname: Union[str, Path]):
        assert out_npz_fname.endswith('.npz')
        np.savez(out_npz_fname, markers=self.markers, labels=self.labels, frame_rate=self.frame_rate)

    def play_mocap_trajectories(self, start_fidx: int = 0, end_fidx: int = -1, ds_rate: int = 1, radius: float = 0.01,
                                delay: int = 0., mocap_rotate=None):
        """
        Visualize the trajectory of markers in 3D.

        :param start_fidx:
        :param end_fidx:
        :param ds_rate:
        :param radius:
        :param delay:
        :param use_cage:
        :param mocap_rotate:
        :return:
        """
        # This is the rotation in X, Y and Z for the mesh in the viewer
        rot = [0, 0, 0] if mocap_rotate is None else mocap_rotate

        # Convert 1D color to 3D color
        def jet(v):
            fourValue = 4 * v
            red = np.min([fourValue - 1.5, -fourValue + 4.5])
            green = np.min([fourValue - 0.5, -fourValue + 3.5])
            blue = np.min([fourValue + 0.5, -fourValue + 2.5])
            result = np.array([[red], [green], [blue]])
            result[result > 1.0] = 1.0
            result[result < 0.0] = 0.0
            return result.reshape((1, -1))

        def create_cage(vs):
            from itertools import product
            return ([Sphere(np.asarray(corner), 1e-10).to_mesh()
                     for corner in product(*zip(np.nanmin(vs, axis=0), np.nanmax(vs, axis=0)))])

        grey = np.ones(3) * .5
        # c3d_data = _marker_data
        end_fidx = len(self.markers) if end_fidx == -1 else end_fidx
        mrkr_frames = self.markers[start_fidx:end_fidx:ds_rate, :, :].copy()
        mrkr_frames = rotate_points_xyz(mrkr_frames, rot).reshape(mrkr_frames.shape)

        mv = MeshViewer(keepalive=False)
        mv.set_background_color(colors['white'])
        mrkr_colors = grey  # Default grey

        cage = []  # create_cage(rotate_points_xyz(mrkr_frames, rot))
        for fIdx, mrkrs in enumerate(mrkr_frames):
            # find indices of markers that are non-zero in current frame

            nan_mask = np.array([False if np.all(~np.isnan(mrkrs[id])) else True for id in range(len(mrkrs))],
                                dtype=bool)
            active = np.nonzero(mrkrs.sum(axis=1))[0]

            # set all subsequent marker colors based on frame 0 (all markers active in frame 0 get a unique color)
            if fIdx == 0:
                mrkr_colors = np.array(
                    [jet(1.0 * i / len(mrkrs[active])) if i in active else grey for i in range(len(mrkrs))],
                    dtype=object)

            mrkr_meshes = [points_to_spheres(mrkrs[~nan_mask], point_color=mrkr_colors[~nan_mask], radius=radius)]

            mv.dynamic_meshes = mrkr_meshes + cage

            if delay > 0: time.sleep(delay)

            mv.titlebar = f'Frame {fIdx:d}/{len(mrkr_frames):d}'


if __name__ == '__main__':
    mocap_fname = '/ps/project/amass/MOCAP/Mixamo/Mixamo_c3d_cleared/Alec/1548Alec_bugbear_2_handed_overhead_bash__hgh_swinghgh_swing_BaseAnimation.c3d'
    mocap = MocapSession(mocap_fname, mocap_unit='m', ignore_stared_labels=False)
    a = mocap.markers_asdict()
    print(a[0])
    print(mocap.labels)
    print(mocap.frame_rate)
    print(mocap._marker_data['labels_perframe'])
    mocap.play_mocap_trajectories(delay=0.1)
