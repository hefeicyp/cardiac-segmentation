from __future__ import division, print_function

import os
import glob
import numpy as np
from math import ceil
from scipy.ndimage.interpolation import map_coordinates
from scipy.ndimage.filters import gaussian_filter

from keras import utils
from keras.preprocessing import image as keras_image
from keras.preprocessing.image import ImageDataGenerator

from . import patient


def load_images(data_dir, mask='both'):
    assert mask in ['inner', 'outer', 'both']

    glob_search = os.path.join(data_dir, "patient*")
    patient_dirs = glob.glob(glob_search)
    if len(patient_dirs) == 0:
        raise Exception("No patient directors found in {}".format(data_dir))

    # load all images into memory (dataset is small)
    images = []
    inner_masks = []
    outer_masks = []
    for patient_dir in patient_dirs:
        p = patient.PatientData(patient_dir)
        images += p.images
        inner_masks += p.endocardium_masks
        outer_masks += p.epicardium_masks

    # reshape to account for channel dimension
    images = np.asarray(images)[:,:,:,None]
    if mask == 'inner':
        masks = np.asarray(inner_masks) // 255
    elif mask == 'outer':
        masks = np.asarray(outer_masks) // 255
    elif mask == 'both':
        masks = np.asarray(inner_masks) // 255 + np.asarray(outer_masks) // 255

    # one-hot encode masks
    dims = masks.shape
    classes = len(set(masks[0].flatten())) # get num classes from first image
    masks = utils.to_categorical(masks).reshape(*dims, classes)

    return images, masks

def random_elastic_deformation(image, alpha, sigma, mode='nearest',
                               random_state=None):
    """Elastic deformation of images as described in [Simard2003]_.
    .. [Simard2003] Simard, Steinkraus and Platt, "Best Practices for
       Convolutional Neural Networks applied to Visual Document Analysis", in
       Proc. of the International Conference on Document Analysis and
       Recognition, 2003.
    """
    assert len(image.shape) == 3

    if random_state is None:
        random_state = np.random.RandomState(None)

    height, width, channels = image.shape

    dx = gaussian_filter(2*random_state.rand(height, width) - 1,
                         sigma, mode="constant", cval=0) * alpha
    dy = gaussian_filter(2*random_state.rand(height, width) - 1,
                         sigma, mode="constant", cval=0) * alpha

    x, y = np.meshgrid(np.arange(height), np.arange(width), indexing='ij')
    indices = (np.repeat(np.ravel(x+dx), channels),
               np.repeat(np.ravel(y+dy), channels),
               np.tile(np.arange(channels), height*width))
    
    values = map_coordinates(image, indices, order=1, mode=mode)

    return values.reshape((height, width, channels))

class Iterator(object):
    def __init__(self, images, masks, batch_size,
                 rotation_range=180,
                 width_shift_range=0.1,
                 height_shift_range=0.1,
                 shear_range=0.1,
                 zoom_range=0.01,
                 fill_mode='nearest',
                 samplewise_center=False,
                 samplewise_std_normalization=False):
        self.images = images
        self.masks = masks
        self.batch_size = batch_size
        augment_options = {
            'rotation_range': rotation_range,
            'width_shift_range': width_shift_range,
            'height_shift_range': height_shift_range,
            'shear_range': shear_range,
            'zoom_range': zoom_range,
            'fill_mode': fill_mode,
        }
        self.idg = ImageDataGenerator(**augment_options)
        self.samplewise_center = samplewise_center
        self.samplewise_std_normalization = samplewise_std_normalization
        self.alpha = kwargs['alpha']
        self.sigma = kwargs['sigma']
        self.fill_mode = kwargs['fill_mode']
        self.i = 0

    def __next__(self):
        return self.next()

    def next(self):
        start = self.i
        end = min(start + self.batch_size, len(self.images))
        self.i += self.batch_size
        if self.i >= len(self.images):
            self.i = 0
        augmented_images = []
        augmented_masks = []
        images_masks = zip(self.images[start:end], self.masks[start:end])
        for image, mask in images_masks:
            _, _, channels = image.shape
            stacked = np.concatenate((image, mask), axis=2)
            augmented = self.idg.random_transform(stacked)
            if self.alpha != 0 and self.sigma != 0:
                augmented = random_elastic_deformation(
                    augmented, self.alpha, self.sigma, self.fill_mode)
            augmented_image = augmented[:,:,:channels]
            if self.samplewise_center:
                augmented_image -= np.mean(augmented_image)
            if self.samplewise_std_normalization:
                augmented_image /= np.std(augmented_image) + 1e-7
            augmented_images.append(augmented_image)
            augmented_masks.append(np.round(augmented[:,:,channels:]))
        return np.asarray(augmented_images), np.asarray(augmented_masks)

def create_generators(data_dir, batch_size, validation_split=0.0, mask='both',
                      augment_training=False, augment_validation=False,
                      augmentation_args={}):
    images, masks = load_images(data_dir, mask)

    # split out last %(validation_split) of images as validation set
    split_index = int((1-validation_split) * len(images))

    normalize_args = {}
    for k in ['samplewise_center', 'samplewise_std_normalization']:
        if k in augmentation_args:
            normalize_args[k] = augmentation_args[k]
    normalize_args = {}

    if augment_training:
        train_generator = Iterator(
            images[:split_index], masks[:split_index],
            batch_size, **augmentation_args)
    else:
        train_generator = ImageDataGenerator(**normalize_args).flow(
            images[:split_index], masks[:split_index],
            batch_size=batch_size)

    train_steps_per_epoch = ceil(split_index / batch_size)

    if validation_split > 0.0:
        if augment_validation:
            val_generator = Iterator(
                images[split_index:], masks[split_index:],
                batch_size, **augmentation_args)
        else:
            val_generator = ImageDataGenerator(**normalize_args).flow(
                images[split_index:], masks[split_index:],
                batch_size=batch_size)
    else:
        val_generator = None

    val_steps_per_epoch = ceil((len(images) - split_index) / batch_size)

    return (train_generator, train_steps_per_epoch,
            val_generator, val_steps_per_epoch)