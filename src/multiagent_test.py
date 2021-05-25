'''
Author: yhu
Contact: phyllis1sjtu@outlook.com
LastEditTime: 2021-05-25 13:50:24
Description: 
'''
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import _init_paths

import os
import json
import cv2
import numpy as np
import time
from progress.bar import Bar
import torch

from external.nms import soft_nms
from opts import opts
from logger import Logger
from utils.utils import AverageMeter
from datasets.dataset_factory import dataset_factory
from detectors.detector_factory import detector_factory


class PrefetchDataset(torch.utils.data.Dataset):
    def __init__(self, opt, dataset, pre_process_func):
        self.samples = dataset.samples
        self.img_dir = dataset.img_dir
        self.pre_process_func = pre_process_func
        self.opt = opt
    
    def load_image_func(self, index):
        sample_id = index // 5
        cam_id = index % 5
        images_key = 'image' if self.opt.coord_mode == 'local' else 'image_g'
        images = []
        trans_mat_list = []
        image_idx = []
        for cam, info in self.samples[sample_id].items():
            if cam.startswith('vehicles'):
                continue
            # else:
            # elif cam.startswith('F'):
            elif cam.endswith(str(cam_id)):
                images.append(cv2.imread(os.path.join(self.img_dir, info[images_key])))
                image_idx.append(info['image_id'])
                trans_mat_list.append(info['trans_mat'])
        # trans_mats = []
        # for trans_mat_i in trans_mat_list:
        #     trans_mats.append([])
        #     for trans_mat_j in trans_mat_list:
        #         trans_mats[-1].append((trans_mat_i.T*trans_mat_j)[None,:])
        #     trans_mats[-1] = np.concatenate(trans_mats[-1], axis=0)[None,:]
        # trans_mats = np.concatenate(trans_mats, axis=0)
        trans_mats = np.zeros([len(images), len(images), 4, 4])
        return images, image_idx, trans_mats

    def __getitem__(self, index):
        images, image_idx, trans_mats = self.load_image_func(index)
        scaled_images, meta = {}, {}
        for scale in opt.test_scales:
            cur_images = []
            for image in images:
                cur_image, cur_meta = self.pre_process_func(image, scale)
                cur_images.append(cur_image)
            scaled_images[scale] = np.concatenate(cur_images, axis=0)
            meta[scale] = cur_meta
        return image_idx, {'images': scaled_images, 'image': images, 'meta': meta, 'trans_mats': trans_mats}

    def __len__(self):
        return len(self.samples)*5


def prefetch_test(opt):
    os.environ['CUDA_VISIBLE_DEVICES'] = opt.gpus_str

    Dataset = dataset_factory[opt.dataset]
    opt = opts().update_dataset_info_and_set_heads(opt, Dataset)
    print(opt)
    Logger(opt)
    Detector = detector_factory[opt.task]

    split = 'val' if not opt.trainval else 'test'
    dataset = Dataset(opt, split)
    detector = Detector(opt)

    data_loader = torch.utils.data.DataLoader(
        PrefetchDataset(opt, dataset, detector.pre_process),
        batch_size=1, shuffle=False, num_workers=1, pin_memory=True)

    results = {}
    num_iters = len(dataset)
    bar = Bar('{}'.format(opt.exp_id), max=num_iters)
    time_stats = ['tot', 'load', 'pre', 'net', 'dec', 'post', 'merge']
    avg_time_stats = {t: AverageMeter() for t in time_stats}
    for ind, (img_idx, pre_processed_images) in enumerate(data_loader):
        ret = detector.run(pre_processed_images)
        for i in range(len(ret['results'])):
            img_id = img_idx[i]
            results[img_id.numpy().astype(np.int32)[0]] = ret['results'][i]
        Bar.suffix = '[{0}/{1}]|Tot: {total:} |ETA: {eta:} '.format(
            ind, num_iters, total=bar.elapsed_td, eta=bar.eta_td)
        for t in avg_time_stats:
            avg_time_stats[t].update(ret[t])
            Bar.suffix = Bar.suffix + '|{} {tm.val:.3f}s ({tm.avg:.3f}s) '.format(
                t, tm=avg_time_stats[t])
        bar.next()
    bar.finish()
    dataset.run_eval(results, opt.save_dir)


if __name__ == '__main__':
    opt = opts().parse()
    prefetch_test(opt)
