import os
import math
from decimal import Decimal

import utility

import torch
import torch.nn as nn
from torch.autograd import Variable
from tqdm import tqdm
import scipy.io as sio
from data import common
import numpy as np


# import model

class Trainer():
    def __init__(self, args, loader, my_model, my_loss, ckp):
        self.args = args
        self.scale = args.scale

        self.ckp = ckp
        self.loader_train = loader.loader_train
        self.loader_test = loader.loader_test
        self.model = my_model

        self.loss = my_loss
        self.optimizer = utility.make_optimizer(args, self.model)
        self.scheduler = utility.make_scheduler(args, self.optimizer)

        if self.args.load != '.':
            self.optimizer.load_state_dict(
                torch.load(os.path.join(ckp.dir, 'optimizer.pt'))
            )
            for _ in range(len(ckp.log)): self.scheduler.step()

        self.error_last = 1e5

    def train(self):
        self.scheduler.step()

        self.loss.step()

        epoch = self.scheduler.last_epoch + 1

        lr = self.scheduler.get_lr()[0]

        self.ckp.write_log(
            '[Epoch {}]\tLearning rate: {:.2e}'.format(epoch, Decimal(lr))
        )
        self.loss.start_log()
        self.model.train()
        # self.model_NLEst.train()
        # self.model_KMEst.train()

        timer_data, timer_model = utility.timer(), utility.timer()
        for batch, (lr, hr, _) in enumerate(self.loader_train):
            lr, hr = self.prepare([lr, hr])
            # print(scale_factor[0,0,0,0])
            timer_data.hold()
            timer_model.tic()
            # _, _, hei, wid = hr.data.size()
            self.optimizer.zero_grad()
            idx_scale = 0

            sr = self.model(lr, idx_scale)
            loss = self.loss(sr, hr)
            if loss.item() < self.args.skip_threshold * self.error_last:
                loss.backward()
                self.optimizer.step()


            else:
                print('Skip this batch {}! (Loss: {})'.format(
                    batch + 1, loss.item()
                ))

            timer_model.hold()

            if (batch + 1) % self.args.print_every == 0:
                self.ckp.write_log('[{}/{}]\t{}\t{:.1f}+{:.1f}s'.format(
                    (batch + 1) * self.args.batch_size,
                    len(self.loader_train.dataset),
                    self.loss.display_loss(batch),
                    timer_model.release(),
                    timer_data.release()))

            timer_data.tic()

        self.loss.end_log(len(self.loader_train))
        self.error_last = self.loss.log[-1, -1]

    def test(self):
        epoch = self.scheduler.last_epoch + 1
        self.ckp.write_log('\nEvaluation:')
        # kernel_test = sio.loadmat('data/Compared_kernels_JPEG_noise_x234.mat')
        scale_list = self.scale  # [2,3,4,8]
        self.ckp.add_log(torch.zeros(1, len(scale_list)))
        self.model.eval()
        no_eval = 0
        # self.model_NLEst.eval()
        # self.model_KMEst.eval()

        timer_test = utility.timer()
        with torch.no_grad():

            for idx_scale, scale in enumerate(scale_list):
                eval_acc = 0
                self.loader_test.dataset.set_scale(idx_scale)

                tqdm_test = tqdm(self.loader_test, ncols=120)
                for idx_img, (lr, hr, filename) in enumerate(tqdm_test):
                    np.random.seed(seed=0)
                    filename = filename[0]
                    # sz = lr.size()
                    # scale_tensor = torch.ones([1, 1, sz[2], sz[3]]).float() * (scale / 80.0)
                    if not no_eval:
                        lr, hr = self.prepare([lr, hr])
                    else:
                        lr = self.prepare([lr])[0]
                    # sz = lr.size()
                    # scale_tensor = torch.ones([1, 1, sz[2], sz[3]]).float() * (2.0 / 80)

                    # print(lr.size())
                    # hr_ = torch.squeeze(hr_)
                    # hr_ = hr_.numpy()
                    # lr = hr

                    sr = self.model(lr, idx_scale)

                    sr = utility.quantize(sr, self.args.rgb_range)

                    save_list = [sr]
                    eval_acc += utility.calc_psnr(
                        sr, hr, scale, self.args.rgb_range,
                        benchmark=self.loader_test.dataset.benchmark
                    )
                    save_list.extend([lr, hr])
                    # # if not no_eval:
                    # #     eval_acc += utility.calc_psnr(
                    # #         sr, hr, scale, self.args.rgb_range,
                    # #         benchmark=self.loader_test.dataset.benchmark
                    # #     )
                    # #     save_list.extend([lr, hr])
                    #
                    if self.args.save_results:
                        self.ckp.save_results(filename, save_list, idx_img, scale)

                self.ckp.log[-1, idx_scale] = eval_acc / len(self.loader_test)
                best = self.ckp.log.max(0)
                self.ckp.write_log(
                    '[{} x{}]\tPSNR: {:.3f} (Best: {:.3f} @epoch {})'.format(
                        self.args.data_test,
                        scale,
                        self.ckp.log[-1, idx_scale],
                        best[0][idx_scale],
                        best[1][idx_scale] + 1
                    )
                )

        self.ckp.write_log(
            'Total time: {:.2f}s\n'.format(timer_test.toc()), refresh=True
        )
        if not self.args.test_only:
            self.ckp.save(self, epoch, is_best=(best[1][0] + 1 == epoch))

    def prepare(self, l, volatile=False):
        device = torch.device('cpu' if self.args.cpu else 'cuda')

        def _prepare(tensor):
            if self.args.precision == 'half': tensor = tensor.half()
            return tensor.to(device)

        return [_prepare(_l) for _l in l]

    def terminate(self):
        if self.args.test_only:
            self.test()
            return True
        else:
            epoch = self.scheduler.last_epoch + 1
            return epoch >= self.args.epochs
