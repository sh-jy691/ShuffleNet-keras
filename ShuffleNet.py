# -*- coding: utf-8 -*-
"""
Created on Thu Apr 25 18:26:41 2019

@author: zjn
"""

import numpy as np
from keras.callbacks import LearningRateScheduler
from keras.models import Model
from keras.layers import Input, Conv2D, Dropout,  Dense, GlobalAveragePooling2D, Concatenate, AveragePooling2D
from keras.layers import Activation, BatchNormalization, add, Reshape, ReLU, DepthwiseConv2D, MaxPooling2D, Lambda
from keras.utils.vis_utils import plot_model
from keras import backend as K
from keras.optimizers import SGD

def _group_conv(x, filters, kernel, stride, groups):
    """
    Group convolution
    
    # Arguments
        x: Tensor, input tensor of with `channels_last` or 'channels_first' data format
        filters: Integer, number of output channels
        kernel: An integer or tuple/list of 2 integers, specifying the
            width and height of the 2D convolution window.
        strides: An integer or tuple/list of 2 integers,
            specifying the strides of the convolution along the width and height.
            Can be a single integer to specify the same value for
            all spatial dimensions.
        groups: Integer, number of groups per channel
        
    # Returns
        Output tensor

    """
    
    channel_axis = 1 if K.image_data_format() == 'channels_first' else -1
    in_channels = K.int_shape(x)[channel_axis]
    
    # number of input channels per group
    nb_ig = in_channels // groups
    # number of output channels per group
    nb_og = filters // groups
    
    gc_list = []
    # Determine whether the number of filters is divisible by the number of groups
    assert filters % groups == 0
    
    for i in range(groups):
        if channel_axis == -1:
            x_group = Lambda(lambda z: z[:, :, :, i * nb_ig: (i + 1) * nb_ig])(x)
        else:
            x_group = Lambda(lambda z: z[:, i * nb_ig: (i + 1) * nb_ig, :, :])(x)
        gc_list.append(Conv2D(filters=nb_og, kernel_size=kernel, strides=stride, 
                              padding='same', use_bias=False)(x_group))
        
    return Concatenate(axis=channel_axis)(gc_list)

def _channel_shuffle(x, groups):
    """
    Channel shuffle layer
    
    # Arguments
        x: Tensor, input tensor of with `channels_last` or 'channels_first' data format
        groups: Integer, number of groups per channel
        
    # Returns
        Shuffled tensor
    """
    
    if K.image_data_format() == 'channels_last':
        height, width, in_channels = K.int_shape(x)[1:]
        channels_per_group = in_channels // groups
        pre_shape = [-1, height, width, groups, channels_per_group]
        dim = (0, 1, 2, 4, 3)
        later_shape = [-1, height, width, in_channels]
    else:
        in_channels, height, width = K.int_shape(x)[1:]
        channels_per_group = in_channels // groups
        pre_shape = [-1, groups, channels_per_group, height, width]
        dim = (0, 2, 1, 3, 4)
        later_shape = [-1, in_channels, height, width]

    x = Lambda(lambda z: K.reshape(z, pre_shape))(x)
    x = Lambda(lambda z: K.permute_dimensions(z, dim))(x)  
    x = Lambda(lambda z: K.reshape(z, later_shape))(x)

    return x

def _shufflenet_unit(inputs, filters, kernel, stride, groups, stage, bottleneck_ratio=0.25):
    """
    ShuffleNet unit
    
    # Arguments
        inputs: Tensor, input tensor of with `channels_last` or 'channels_first' data format
        filters: Integer, number of output channels
        kernel: An integer or tuple/list of 2 integers, specifying the
            width and height of the 2D convolution window.
        strides: An integer or tuple/list of 2 integers,
            specifying the strides of the convolution along the width and height.
            Can be a single integer to specify the same value for
            all spatial dimensions.
        groups: Integer, number of groups per channel
        stage: Integer, stage number of ShuffleNet
        bottleneck_channels: Float, bottleneck ratio implies the ratio of bottleneck channels to output channels
         
    # Returns
        Output tensor
        
    # Note
        For Stage 2, we(authors of shufflenet) do not apply group convolution on the first pointwise layer 
        because the number of input channels is relatively small.
    """
    channel_axis = 1 if K.image_data_format() == 'channels_first' else -1
    in_channels = K.int_shape(inputs)[channel_axis]
    bottleneck_channels = int(filters * bottleneck_ratio)
    
    if stage == 2:
        x = Conv2D(filters=bottleneck_channels, kernel_size=kernel, strides=1,
                   padding='same', use_bias=False)(inputs)
    else:
        x = _group_conv(inputs, bottleneck_channels, (1, 1), 1, groups)
    x = BatchNormalization(axis=channel_axis)(x)
    x = ReLU()(x)
    
    x = _channel_shuffle(x, groups)
    
    x = DepthwiseConv2D(kernel_size=kernel, strides=stride, depth_multiplier=1, 
                        padding='same', use_bias=False)(x)
    x = BatchNormalization(axis=channel_axis)(x)
      
    if stride == 2:
        x = _group_conv(x, filters - in_channels, (1, 1), 1, groups)
        x = BatchNormalization(axis=channel_axis)(x)
        avg = AveragePooling2D(pool_size=(3, 3), strides=2, padding='same')(inputs)
        x = Concatenate(axis=channel_axis)([x, avg])
    else:
        x = _group_conv(x, filters, (1, 1), 1, groups)
        x = BatchNormalization(axis=channel_axis)(x)
        x = add([x, inputs])
    
    return x

def _stage(x, filters, kernel, groups, repeat, stage):
    """
    Stage of ShuffleNet
    
    # Arguments
        x: Tensor, input tensor of with `channels_last` or 'channels_first' data format
        filters: Integer, number of output channels
        kernel: An integer or tuple/list of 2 integers, specifying the
            width and height of the 2D convolution window.
        strides: An integer or tuple/list of 2 integers,
            specifying the strides of the convolution along the width and height.
            Can be a single integer to specify the same value for
            all spatial dimensions.
        groups: Integer, number of groups per channel
        repeat: Integer, total number of repetitions for a shuffle unit in every stage
        stage: Integer, stage number of ShuffleNet
        
    # Returns
        Output tensor
    """
    x = _shufflenet_unit(x, filters, kernel, 2, groups, stage)
    
    for i in range(1, repeat):
        x = _shufflenet_unit(x, filters, kernel, 1, groups, stage)
        
    return x
    
def ShuffleNet(input_shape, classes):
    """
    ShuffleNet architectures
    
    # Arguments
        input_shape: An integer or tuple/list of 3 integers, shape
            of input tensor
        k: Integer, number of classes to predict
        
    # Returns
        A keras model
    """
    inputs = Input(shape=input_shape)
    
    x = Conv2D(24, (3, 3), strides=2, padding='same', use_bias=True, activation='relu')(inputs)
    x = MaxPooling2D(pool_size=(3, 3), strides=2, padding='same')(x)
    
    x = _stage(x, filters=384, kernel=(3, 3), groups=8, repeat=4, stage=2)
    x = _stage(x, filters=768, kernel=(3, 3), groups=8, repeat=8, stage=3)
    x = _stage(x, filters=1536, kernel=(3, 3), groups=8, repeat=4, stage=4)
    
    x = GlobalAveragePooling2D()(x)
    
    x = Dense(classes)(x)
    predicts = Activation('softmax')(x)
    
    model = Model(inputs, predicts)
    
    return model
    
if __name__ == '__main__':
    model = ShuffleNet((224, 224, 3), 1000)
    plot_model(model, to_file='ShuffleNet.png', show_shapes=True)