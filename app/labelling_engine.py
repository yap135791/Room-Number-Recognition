import os
import glob
import logging
import datetime
from queue import Full, Empty
from collections import Counter
from multiprocessing import Process, Queue

import torch
import numpy as np
import cv2

class CheckerModel:
    '''
    CheckeModel is a wrapper for Checker Model, which is made with tensorflow.
    @param cfg: cfg['labelling_engine']['model1']
    '''
    def __init__(self, cfg):
        self.model = cv2.dnn.readNetFromTensorflow(cfg['path'])
        self.input_layer = cfg['input_layer']
        self.output_layers = cfg['output_layers']
    
    def predict(self, img):
        '''
        Returns true if the img contains image.
        @param img: image
        @return: result
        '''
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        gray = np.reshape(gray, (48, 48, 1))
        blob = cv2.dnn.blobFromImage(gray, 1./255, (48, 48), (0, 0, 0), False, False)
        self.model.setInput(blob, self.input_layer)
        output = self.model.forward()
        is_target = np.argmax(output[0])
        return True if is_target == 0 else False

class SVHNModel:
    '''
    CheckeModel is a wrapper for SVHN Model, which is made with YOLO darknet.
    @param cfg: cfg['labelling_engine']['model2']
    '''
    def __init__(self, cfg):
        self.repo = cfg['repository']
        self.func = cfg['function']
        self.model = torch.hub.load(self.repo, self.func).fuse().eval()
        self.model = self.model.autoshape()

    def predict(self, img):
        '''
        Returns label.
        @param img: rgb image
        @return: label
        '''
        _img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        with torch.no_grad():
            prediction = self.model(_img, size=640)
        if prediction[0] is None:
            return None
        
        boxes = list()
        for pred in prediction:
            for x1, y1, x2, y2, conf, clas in pred: # xyxy, confidence, class
                boxes.append({
                    'x1': x1,
                    'y1': y1,
                    'x2': x2,
                    'y2': y2,
                    'conf': conf,
                    'class': int(clas)
                })

        boxes = sorted(boxes, key=lambda x: x['x1'])
        if len(boxes) <= 2:
            return None
        label = self.make_label(boxes)
        return label

    def make_label(self, boxes):
        '''
        Convert list of boxes to label string.
        @param boxes: list of boxes
        @return: label string
        '''
        chars = []
        for it in [str(box['class']) for box in boxes]:
            if it == '10':
                chars.append('0')
            else:
                chars.append(it)

        if len(chars) == 3:
            return ''.join(chars)
        elif len(chars) >= 4:
            return ''.join(chars[:3]) + '-' + chars[3]
        else:
            return ''


class LabellingEngine:
    '''
    LabellingEngine is a class for managing models, queues and subprocess.
    @param cfg: cfg['labelling_engine']
    '''
    def __init__(self, cfg):
        # Logging
        self.init_logger()

        # Variables
        self.model1_cfg = cfg['model1']
        self.model2_cfg = cfg['model2']
        self.flag_for_save_img = cfg['flag_for_save_img']
        self.path_for_noise = cfg['path_for_noise']
        self.path_for_num = cfg['path_for_num']
        self.output_queue = []
        self.output_queue_cap = cfg['output_queue_capacity']
        self.most_frequent_label = ''

        # Init models
        self.model1 = CheckerModel(self.model1_cfg)
        self.model2 = SVHNModel(self.model2_cfg)
        self.logger.info('Imported all models successfully.')
        self.idx = 0

        # Path
        if self.flag_for_save_img:
            if not os.path.exists(self.path_for_num):
                os.makedirs(self.path_for_num)
            else:
                files = glob.glob(self.path_for_num + '/*')
                for f in files:
                    os.remove(f)

            if not os.path.exists(self.path_for_noise):
                os.makedirs(self.path_for_noise)
            else:
                files = glob.glob(self.path_for_noise + '/*')
                for f in files:
                    os.remove(f)

    def init_logger(self):
        '''
        Initiate a logger for LabellingEngine.
        '''
        logger = logging.getLogger("Main.LabellingEngine")
        logger.setLevel(logging.INFO)
        self.logger = logger

    def predict(self, img, bigimg):
        '''
        Predict the image.
        @param img: 48*48 rgb image
        @param bigimg:
        @return: string of the doorplate number.
            'Noise' when it is noise,
            'NaN' when the SVHN model failed to predict.
        @return: the most frequent label
        @return: flag that the image contains numbers
        '''
        is_noise = self.model1.predict(img)

        if self.flag_for_save_img:
            path = self.path_for_noise if is_noise else self.path_for_num
            path = (path + '/{}.png').format(self.idx)
            save_img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            cv2.imwrite(path, save_img)
            self.idx += 1

        if is_noise:
            return 'Noise', self.most_frequent_label, False

        now = self.model2.predict(bigimg)
        if now is None:
            return 'NaN', self.most_frequent_label, False

        self.most_frequent_label = self.get_most_frequent_label(now)
        return now, self.most_frequent_label, True

    def get_most_frequent_label(self, now=None):
        '''
        Get the most frequent predicted label from output queue.
        @param now: new label
        @return: the most frequent label string
        '''
        func = lambda q: Counter(q).most_common(1)[0][0]
        if now is None:
            if len(self.output_queue) == 0:
                return ''
            return func(self.output_queue)
        
        self.output_queue.append(now)
        if len(self.output_queue) > self.output_queue_cap:
            self.output_queue = self.output_queue[1:]
        return func(self.output_queue)

    def clear_most_frequent_label(self):
        '''
        Clear the most frequent label variable and the output queue.
        '''
        self.most_frequent_label = ''
        self.output_queue.clear()

    def close(self):
        '''
        Close all models.
        '''
        pass
