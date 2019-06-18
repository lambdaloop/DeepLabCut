'''
Adapted from original predict.py by Eldar Insafutdinov's implementation of [DeeperCut](https://github.com/eldar/pose-tensorflow)
To do faster inference on videos. See https://www.biorxiv.org/content/early/2018/10/30/457242

Source: DeeperCut by Eldar Insafutdinov
https://github.com/eldar/pose-tensorflow
'''

import numpy as np
import tensorflow as tf
from deeplabcut.pose_estimation_tensorflow.nnet.net_factory import pose_net

def setup_pose_prediction(cfg):
    tf.reset_default_graph()
    inputs = tf.placeholder(tf.float32, shape=[cfg.batch_size   , None, None, 3])
    net_heads = pose_net(cfg).test(inputs)
    outputs = [net_heads['part_prob']]
    if cfg.location_refinement:
        outputs.append(net_heads['locref'])

    restorer = tf.train.Saver()
    sess = tf.Session()
    sess.run(tf.global_variables_initializer())
    sess.run(tf.local_variables_initializer())

    # Restore variables from disk.
    restorer.restore(sess, cfg.init_weights)

    return sess, inputs, outputs
    
def extract_cnn_output(outputs_np, cfg):
    ''' extract locref + scmap from network '''
    scmap = outputs_np[0]
    scmap = np.squeeze(scmap)
    locref = None
    if cfg.location_refinement:
        locref = np.squeeze(outputs_np[1])
        shape = locref.shape
        locref = np.reshape(locref, (shape[0], shape[1], -1, 2))
        locref *= cfg.locref_stdev
    if len(scmap.shape)==2: #for single body part!
        scmap=np.expand_dims(scmap,axis=2)
    return scmap, locref

def argmax_pose_predict(scmap, offmat, stride):
    """Combine scoremat and offsets to the final pose."""
    num_joints = scmap.shape[2]
    pose = []
    for joint_idx in range(num_joints):
        maxloc = np.unravel_index(np.argmax(scmap[:, :, joint_idx]),
                                  scmap[:, :, joint_idx].shape)
        offset = np.array(offmat[maxloc][joint_idx])[::-1]
        pos_f8 = (np.array(maxloc).astype('float') * stride + 0.5 * stride +
                  offset)
        pose.append(np.hstack((pos_f8[::-1],
                               [scmap[maxloc][joint_idx]])))
    return np.array(pose)

def multi_pose_predict(scmap, locref, stride, num_outputs):
    Y, X = get_top_values(scmap[None], num_outputs)
    Y, X = Y[:, 0], X[:, 0]
    num_joints = scmap.shape[2]
    DZ=np.zeros((num_outputs,num_joints,3)) 
    for m in range(num_outputs):
        for k in range(num_joints):
            x = X[m, k]
            y = Y[m, k]
            DZ[m,k,:2]=locref[y,x,k,:]
            DZ[m,k,2]=scmap[y,x,k]
            
    X = X.astype('float32')*stride + .5*stride + DZ[:,:,0]
    Y = Y.astype('float32')*stride + .5*stride + DZ[:,:,1]
    P = DZ[:, :, 2]
    
    pose = np.empty((num_joints, num_outputs*3), dtype='float32') 
    pose[:,0::3] = X.T
    pose[:,1::3] = Y.T
    pose[:,2::3] = P.T

    return pose
    
def getpose(image, cfg, sess, inputs, outputs, outall=False):
    ''' Extract pose '''
    im=np.expand_dims(image, axis=0).astype(float)
    outputs_np = sess.run(outputs, feed_dict={inputs: im})
    scmap, locref = extract_cnn_output(outputs_np, cfg)
    num_outputs = cfg.get('num_outputs', 1)
    if num_outputs > 1:
        pose = multi_pose_predict(scmap, locref, cfg.stride, num_outputs)
    else:
        pose = argmax_pose_predict(scmap, locref, cfg.stride)
    if outall:
        return scmap, locref, pose
    else:
        return pose

## Functions below implement are for batch sizes > 1:
def extract_cnn_outputmulti(outputs_np, cfg):
    ''' extract locref + scmap from network 
    Dimensions: image batch x imagedim1 x imagedim2 x bodypart'''
    scmap = outputs_np[0]
    locref = None
    if cfg.location_refinement:
        locref =outputs_np[1]
        shape = locref.shape
        locref = np.reshape(locref, (shape[0], shape[1],shape[2], -1, 2))
        locref *= cfg.locref_stdev
    if len(scmap.shape)==2: #for single body part!
        scmap=np.expand_dims(scmap,axis=2)
    return scmap, locref


def get_top_values(scmap, n_top=5):
    batchsize,ny,nx,num_joints = scmap.shape
    scmap_flat = scmap.reshape(batchsize,nx*ny,num_joints)
    if n_top == 1:
        scmap_top = np.argmax(scmap_flat, axis=1)[None]
    else:
        scmap_top = np.argpartition(scmap_flat, -n_top, axis=1)[:, -n_top:]
        for ix in range(batchsize):
            vals = scmap_flat[ix, scmap_top[ix], np.arange(num_joints)]
            arg = np.argsort(-vals, axis=0)
            scmap_top[ix] = scmap_top[ix, arg, np.arange(num_joints)]
        scmap_top = scmap_top.swapaxes(0,1)

    Y, X = np.unravel_index(scmap_top, (ny, nx))
    return Y, X

def getposeNP(image, cfg, sess, inputs, outputs, outall=False):
    ''' Adapted from DeeperCut, performs numpy-based faster inference on batches'''
    num_outputs = cfg.get('num_outputs', 1)
    
    outputs_np = sess.run(outputs, feed_dict={inputs: image})
    
    scmap, locref = extract_cnn_outputmulti(outputs_np, cfg) #processes image batch.
    batchsize,ny,nx,num_joints = scmap.shape

    Y,X = get_top_values(scmap, n_top=num_outputs)
    
    #Combine scoremat and offsets to the final pose.
    DZ=np.zeros((num_outputs,batchsize,num_joints,3)) 
    for m in range(num_outputs):
        for l in range(batchsize):
            for k in range(num_joints):
                x = X[m, l, k]
                y = Y[m, l, k]
                DZ[m,l,k,:2]=locref[l,y,x,k,:]
                DZ[m,l,k,2]=scmap[l,y,x,k]
            
    X = X.astype('float32')*cfg.stride + .5*cfg.stride + DZ[:,:,:,0]
    Y = Y.astype('float32')*cfg.stride + .5*cfg.stride + DZ[:,:,:,1]
    P = DZ[:,:,:,2]

    Xs = X.swapaxes(0,2).swapaxes(0,1)
    Ys = Y.swapaxes(0,2).swapaxes(0,1)
    Ps = P.swapaxes(0,2).swapaxes(0,1)
    
    pose = np.empty((cfg['batch_size'], num_outputs*cfg['num_joints']*3), dtype=X.dtype) 
    pose[:,0::3] = Xs.reshape(batchsize, -1)
    pose[:,1::3] = Ys.reshape(batchsize, -1)
    pose[:,2::3] = Ps.reshape(batchsize, -1)
    
    if outall:
        return scmap, locref, pose
    else:
        return pose

