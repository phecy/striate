import numpy as np
from time import time
import pycuda
import pycuda.autoinit
from pycuda import gpuarray
from pycuda.gpuarray import GPUArray
import scikits.cuda
import scikits.cuda.linalg
from pycuda.elementwise import ElementwiseKernel

scikits.cuda.linalg.init()

import scikits.cuda.cublas as cublas
cublas_handle =  cublas.cublasCreate()


from pycuda.compiler import SourceModule


class Timer:
  def __init__(self):
    self.func_time = {}
    self.last_time = 0.0

  def start(self):
    self.last_time = time()

  def end(self, func_name):
    ftime = time() - self.last_time
    if func_name in self.func_time:
      self.func_time[func_name] += ftime
    else:
      self.func_time[func_name] = ftime

  def report(self):
    dic = self.func_time
    for key in dic:
      print key, ':', dic[key]

timer = Timer()


INTERNAL_SIZE = 256


def I(i): return np.int32(i)
def F(f): return np.float32(f)
def NVBLOCK(x, base):
  if x / base * base == x:
    return x / base
  else:
    return x /base + 1

def row_max_reduce(x, mat):
  '''
  Return the max of each row to a vec, ONLY work on small matrix
  Small means the column of the matrix is up to 1024
  and the rows, seams like a little big, can be 2048, but the upper bound has  not been tested
  '''
  timer.start()
  mh, mw = mat.shape
  vh, vw = x.shape

  assert(vw == 1 and vh == mh or vh == 1 and vw == mh)

  mod = SourceModule('''
    __global__
    void row_max_reduce(float* mat, float* vec, int leading, int rows, int cols) {
    const int INTERNAL_SIZE = 256;
    int i = blockDim.x * blockIdx.x + threadIdx.x;
    int j = blockDim.y * blockIdx.y + threadIdx.y;

    __shared__ float buffer[INTERNAL_SIZE];
    if(i < cols && i < INTERNAL_SIZE)
      buffer[i] = mat[i + j * leading];
    __syncthreads();

    int index = 1;
    if(cols > INTERNAL_SIZE) {
      if(threadIdx.x < INTERNAL_SIZE ) {
        int forwardInd = threadIdx.x + index * INTERNAL_SIZE;
        while(forwardInd < cols) {
          if (buffer[threadIdx.x] < mat[forwardInd + j* leading])
            buffer[threadIdx.x] = mat[forwardInd + j * leading];
          index ++;
          forwardInd = threadIdx.x + index * INTERNAL_SIZE;
        }
      }
    }
    __syncthreads();

    int total = INTERNAL_SIZE > cols? cols : INTERNAL_SIZE;
    while(total > 1) {
      int halfPoint = ((1+total) >> 1);
      if (threadIdx.x < halfPoint)  {
        if(threadIdx.x+halfPoint < total) {
          if(buffer[threadIdx.x] < buffer[threadIdx.x + halfPoint])
            buffer[threadIdx.x] = buffer[threadIdx.x + halfPoint];
        }
      }
      __syncthreads();
      total = ((1+total) >> 1);
    }
    __syncthreads();
    if(threadIdx.x == 0)
      vec[blockIdx.y] = buffer[0];
   }'''
   )
  row_max_reduce_func = mod.get_function('row_max_reduce')
  grid = (1,mh)
  block = (mw, 1,  1)
  leading = mat.strides[0]/4
  row_max_reduce_func(mat, x, I(leading), I(mh), I(mw), block = block, grid= grid)
  timer.end("row_max_reduce")


def col_max_reduce(x, mat):
  '''
  Return the max of each column to a vec, ONLY work on small matrix
  Small means the row of the matrix is up to 1024
  and the column, seams like a little big, can be 2048, but the upper bound has  not been tested
  '''
  timer.start()
  mh, mw = mat.shape
  vh, vw = x.shape
  assert(vw == 1 and vh == mw or vh == 1 and vw == mw)

  mod = SourceModule('''
    __global__
    void col_max_reduce(float* mat, float* vec, int leading, int rows, int cols) {
    const int INTERNAL_SIZE = 256;
    int i = blockDim.x * blockIdx.x + threadIdx.x;
    int j = blockDim.y * blockIdx.y + threadIdx.y;

    __shared__ float buffer[INTERNAL_SIZE];
    if(j < INTERNAL_SIZE && j < rows)
      buffer[j] = mat[i + j * leading];
    __syncthreads();

    int index = 1;
    if(rows > INTERNAL_SIZE) {
      if(threadIdx.y < INTERNAL_SIZE) {
        int forwardInd = threadIdx.y + index * INTERNAL_SIZE;
        while(forwardInd < rows) {
          if (buffer[threadIdx.y] < mat[i +forwardInd * leading])
            buffer[threadIdx.y] = mat[i  + forwardInd * leading];
          index ++;
          forwardInd = threadIdx.y + index * INTERNAL_SIZE;
        }
      }
    }
    __syncthreads();

    int total = INTERNAL_SIZE > rows ? rows : INTERNAL_SIZE;
    while(total > 1) {
      int halfPoint = ((1+total) >> 1);
      if (threadIdx.y < halfPoint)  {
        if(threadIdx.y+halfPoint < total) {
          if(buffer[threadIdx.y] < buffer[threadIdx.y + halfPoint])
            buffer[threadIdx.y] = buffer[threadIdx.y + halfPoint];
        }
      }
      __syncthreads();
      total = ((1+total) >> 1);
    }
    __syncthreads();
    if(threadIdx.y == 0)
      vec[i] = buffer[0];
   }'''
   )
  col_max_reduce_func = mod.get_function('col_max_reduce')
  grid = (mw, 1)
  block = (1, mh,   1)
  leading = mat.strides[0]/4
  col_max_reduce_func(mat, x, I(leading), I(mh), I(mw), block = block, grid= grid)
  timer.end('col_max_reduce')


def find_row_max_id(x, mat):
  '''
  Return the id of max in each row to a vec(0-based), ONLY work on small matrix
  Small means the column of the matrix is up to 1024
  and the rows, seams like a little big, can be 2048, but the upper bound has  not been tested
  '''
  timer.start()
  mh, mw = mat.shape
  vh, vw = x.shape
  assert(vw == 1 and vh == mh or vh == 1 and vw == mh)

  mod = SourceModule('''
    __global__
    void row_max_id(float* mat, float* vec, int leading, int rows, int cols) {
    const int INTERNAL_SIZE = 256;
    int i = blockDim.x * blockIdx.x + threadIdx.x;
    int j = blockDim.y * blockIdx.y + threadIdx.y;

    __shared__ float buffer[INTERNAL_SIZE];
    __shared__ int mind[INTERNAL_SIZE];
    if(i < INTERNAL_SIZE && i < cols){
      buffer[i] = mat[i + j * leading];
      mind[i] = threadIdx.x;
    }
    __syncthreads();

    int index = 1;
    if(cols > INTERNAL_SIZE)  {
      if(threadIdx.x < INTERNAL_SIZE) {
        int forwardInd = threadIdx.x + index * INTERNAL_SIZE;
        while(forwardInd < cols)  {
          if (buffer[threadIdx.x] < mat[forwardInd + j * leading]) {
            buffer[threadIdx.x] = mat[forwardInd + j * leading];
            mind[threadIdx.x] = forwardInd;
          }
          index ++;
          forwardInd = threadIdx.x + index * INTERNAL_SIZE;
        }
      }
    }
    __syncthreads();

    int total = INTERNAL_SIZE > cols ? cols : INTERNAL_SIZE;
    while(total > 1) {
      int halfPoint = ((1+total) >> 1);
      if (threadIdx.x < halfPoint)  {
        if(threadIdx.x+halfPoint < total) {
          if(buffer[threadIdx.x] < buffer[threadIdx.x + halfPoint]) {
            buffer[threadIdx.x] = buffer[threadIdx.x + halfPoint];
            mind[threadIdx.x] = mind[threadIdx.x + halfPoint];
          }
        }
      }
      __syncthreads();
      total = ((1+total) >> 1);
    }
    __syncthreads();
    if(threadIdx.x == 0)
      vec[blockIdx.y] = mind[0];
   }'''
   )
  row_max_id = mod.get_function('row_max_id')
  grid = (1, mh)
  block = (mw, 1,  1)
  leading = mat.strides[0]/4
  row_max_id(mat, x, I(leading), I(mh), I(mw), block = block, grid= grid)
  timer.end('find_row_max_id')


def find_col_max_id(x, mat):
  '''
  Return the id of max in each column to a vec, ONLY work on small matrix
  Small means the row of the matrix is up to 1024
  and the column, seams like a little big, can be 2048, but the upper bound has  not been tested
  '''
  timer.start()
  mh, mw = mat.shape
  vh, vw = x.shape
  assert(vw == 1 and vh == mw or vh == 1 and vw == mw)

  mod = SourceModule('''
    __global__
    void col_max_id(float* mat, float* vec, int leading, int rows, int cols) {
    const int INTERNAL_SIZE = 256;
    int i = blockDim.x * blockIdx.x + threadIdx.x;
    int j = blockDim.y * blockIdx.y + threadIdx.y;

    __shared__ float buffer[INTERNAL_SIZE];
    __shared__ int mind[INTERNAL_SIZE];
    if( j < INTERNAL_SIZE && j < rows){
      buffer[j] = mat[i + j * leading];
      mind[j] = threadIdx.y;
     }
    __syncthreads();

    int index = 1;
    if(rows > INTERNAL_SIZE) {
      if(threadIdx.y < INTERNAL_SIZE ){
        int forwardInd = threadIdx.y + index * INTERNAL_SIZE;
        while(forwardInd < rows) {
          if (buffer[threadIdx.y] < mat[i + forwardInd * leading]) {
            buffer[threadIdx.y] = mat[i + forwardInd * leading];
            mind[threadIdx.y] = forwardInd;
          }
          index ++;
          forwardInd = threadIdx.y + index * INTERNAL_SIZE;
        }
      }
    }
    __syncthreads();

    int total = INTERNAL_SIZE > rows ? rows : INTERNAL_SIZE;
    while(total > 1) {
      int halfPoint = ((1+total) >> 1);
      if (threadIdx.y < halfPoint)  {
        if(threadIdx.y+halfPoint < total) {
          if(buffer[threadIdx.y] < buffer[threadIdx.y  + halfPoint]) {
            buffer[threadIdx.y] = buffer[threadIdx.y + halfPoint];
            mind[threadIdx.y] = mind[threadIdx.y + halfPoint];
          }
        }
      }
      __syncthreads();
      total = ((1+total) >> 1);
    }
    __syncthreads();
    if(threadIdx.y == 0)
      vec[i] = mind[0];
   }'''
   )
  col_max_id = mod.get_function('col_max_id')
  grid = (mw, 1)
  block = (1, mh, 1)
  leading = mat.strides[0]/4

  col_max_id(mat, x, I(leading), I(mh), I(mw), block = block, grid= grid)
  timer.end('find_col_max_id')



def add_vec_to_rows(mat, vec, dest = None,  alpha = 1.0, beta = 1.0):
  '''
  Add the element in vec to every element in mat in corresponding rows
  The function behaves exactly like mat + vec in numpy
  '''
  timer.start()
  mh, mw = mat.shape
  vh, vw = vec.shape

  assert(vw == 1 and vh == mh or vh == 1 and vw == mh)
  mod = SourceModule('''
    __global__
    void add_vec_to_rows( float alpha, float* row, float beta, float* mat, float* dst,int leading, int rows, int cols) {
      int i = blockIdx.x * blockDim.x + threadIdx.x;
      int j = blockIdx.y * blockDim.y + threadIdx.y;
      int index = i + j*leading;
      if ( i < cols   &&  j < rows)
        dst[index] = alpha* row[j] + beta * mat[index];
    }'''
    )

  add_func = mod.get_function('add_vec_to_rows')
  if dest is None:
    dest = mat
  block = (32, 32, 1)
  grid = (NVBLOCK(mw, 32), NVBLOCK(mh, 32))
  leading = mat.strides[0]/4
  add_func(F(alpha), vec, F(beta), mat, dest, I(leading), I(mh), I(mw), block = block, grid = grid)
  timer.end('add_vec_to_rows')

def add_vec_to_cols(mat, vec, dest = None,  alpha = 1.0, beta = 1.0):
  '''
  Add the element in vec to every element in mat in corresponding cols
  The function behaves exactly like mat + vec in numpy
  '''
  timer.start()
  mh, mw = mat.shape
  vh, vw = vec.shape

  assert(vw == 1 and vh == mw or vh == 1 and vw == mw)
  mod = SourceModule('''
    __global__
    void add_vec_to_cols( float alpha, float* row, float beta, float* mat, float* dst,int leading, int rows, int cols) {
      int i = blockIdx.x * blockDim.x + threadIdx.x;
      int j = blockIdx.y * blockDim.y + threadIdx.y;
      int index = i + j*leading;
      if ( i < cols   &&  j < rows)
        dst[index] = alpha* row[i] + beta * mat[index];
    }'''
    )

  add_func = mod.get_function('add_vec_to_cols')
  if not dest:
    dest = mat
  block = (32, 32, 1)
  grid = (NVBLOCK(mw, 32), NVBLOCK(mh, 32))
  leading = mat.strides[0] / 4
  add_func(F(alpha), vec,  F(beta), mat, dest, I(leading), I(mh), I(mw),  block = block, grid = grid)
  timer.end('add_vec_to_cols')


def div_vec_to_rows(mat, vec, dest = None):
  '''
  Divide the element in corresponding row of matrix by the element in the vec
  '''
  timer.start()
  mh, mw = mat.shape
  vh, vw = vec.shape

  assert(vw == 1 and vh == mh or vh == 1 and vw == mh)
  mod = SourceModule('''
    __global__
    void div_vec_to_rows(float* row, float* mat, float* dst,int leading, int rows, int cols) {
      int i = blockIdx.x * blockDim.x + threadIdx.x;
      int j = blockIdx.y * blockDim.y + threadIdx.y;
      int index = i + j*leading;
      if ( i < cols   &&  j < rows)
        dst[index] = mat[index] / row[j];
    }'''
    )

  div_func = mod.get_function('div_vec_to_rows')
  if not dest:
    dest = mat
  block = (32, 32, 1)
  grid = (NVBLOCK(mw, 32), NVBLOCK(mh, 32))
  leading = mat.strides[0] /4
  div_func( vec,  mat, dest, I(leading),I(mh), I(mw), block = block, grid = grid)
  timer.end('div_vec_to_rows')



def div_vec_to_cols(mat, vec, dest = None):
  '''
  Divide the element in corresponding column of matrix by the element in the vec
  '''
  timer.start()
  mh, mw = mat.shape
  vh, vw = vec.shape

  assert(vw == 1 and vh == mw or vh == 1 and vw == mw)
  mod = SourceModule('''
    __global__
    void div_vec_to_cols(float* row, float* mat, float* dst,int leading, int rows, int cols) {
      int i = blockIdx.x * blockDim.x + threadIdx.x;
      int j = blockIdx.y * blockDim.y + threadIdx.y;
      int index = i + j*leading;
      if ( i < cols   &&  j < rows)
        dst[index] = mat[index] / row[i];
    }'''
    )

  div_func = mod.get_function('div_vec_to_cols')
  if not dest:
    dest = mat
  block = (32, 32, 1)
  grid = (NVBLOCK(mw , 32), NVBLOCK(mh, 32))
  leading = mat.strides[0] /4
  div_func(vec, mat, dest, I(leading), I(mh), I(mw), block = block, grid = grid)
  timer.end('div_vec_to_cols')



def add_row_sum_to_vec(vec, mat, alpha = 1.0, beta = 1.0):
  '''
  This function would sum up the element int a matrix row and store the result to
  the corresponding position of the vec
  Unlike other function that only provide small computation, this function raise the
  upper bound for the number of column to 2^16, actually it could be 2^20
  '''
  timer.start()
  mh, mw = mat.shape
  vh, vw = vec.shape
  assert(vw == 1 and vh == mh or vh == 1 and vw == mh)

  mod = SourceModule('''
  __global__ void add_row_sum(float* mat, float alpha, float* vec, float beta, int leading, int
  rows, int cols) {
    const int INTERNAL_SIZE = 256;
    int i = blockDim.x * blockIdx.x + threadIdx.x;
    int j = blockDim.y * blockIdx.y + threadIdx.y;

    __shared__ float buffer[INTERNAL_SIZE];
    if(i < cols)
      buffer[threadIdx.x] = mat[i + j * leading];
    __syncthreads();

    int total = INTERNAL_SIZE > cols ? cols : INTERNAL_SIZE;
    while(total > 1) {
      int halfPoint = ((1+total) >> 1);
      if (threadIdx.x < halfPoint && i < cols)  {
        float temp = 0.0;
        if(threadIdx.x+halfPoint < total && i + halfPoint < cols) {
          temp = buffer[threadIdx.x + halfPoint];
        }
        buffer[threadIdx.x] += temp;
      }
      __syncthreads();
      total = ((1+total) >> 1);
    }
    __syncthreads();

    if(threadIdx.x == 0)
      vec[blockIdx.y * gridDim.x + blockIdx.x]  = alpha* vec[blockIdx.y * gridDim.x + blockIdx.x] + beta * buffer[0];
      //vec[j] = alpha*vec[j] + beta * buffer[0];
  }'''
  )

  add_row_sum = mod.get_function('add_row_sum')
  if mat.shape[1] <= INTERNAL_SIZE:
    grid = (1, mh)
    block = (mw, 1,  1)
    leading = mat.strides[0] /4
    add_row_sum(mat, F(alpha), vec, F(beta),I(leading), I(mh), I(mw), block = block, grid= grid)
  else:
    block = (INTERNAL_SIZE, 1, 1)
    grid = (NVBLOCK(mw, INTERNAL_SIZE), mh)
    tmp  = gpuarray.to_gpu(np.zeros((mh, NVBLOCK(mw, INTERNAL_SIZE)) ).astype(np.float32))
    leading = mat.strides[0]/4
    add_row_sum(mat, F(alpha), tmp, F(beta), I(leading), I(mh),I(mw), block = block, grid = grid)
    add_row_sum_to_vec(vec, tmp)

  timer.end('add_row_sum_to_vec')


def add_col_sum_to_vec(vec, mat, alpha = 1.0, beta = 1.0):
  '''
  This function would sum up the element int a matrix column and store the result to
  the corresponding position of the vec
  ONLY work on small matrix
  Small means the row of the matrix is up to 1024
  and the column, seams like a little big, can be 2048, but the upper bound has  not been tested
  '''
  timer.start()
  mh, mw = mat.shape
  vh, vw = vec.shape
  assert(vw == 1 and vh == mw or vh == 1 and vw == mw)

  mod = SourceModule('''
  __global__ void add_col_sum(float* mat, float alpha, float* vec, float beta, int leading, int
  rows, int cols) {
  /*
    vec[blockIdx.x] = 0;
    for (int i = 0; i < rows; ++i) {
      vec[blockIdx.x] += mat[cols * i + blockIdx.x];
    }
    return;
*/
    const int INTERNAL_SIZE = 256;
    int i = blockDim.x * blockIdx.x + threadIdx.x;
    int j = blockDim.y * blockIdx.y + threadIdx.y;

    __shared__ float buffer[INTERNAL_SIZE];
    if(j < INTERNAL_SIZE && j < rows)
      buffer[j] = mat[i + j * cols];

    __syncthreads();

    int index = 1;
    if(rows > INTERNAL_SIZE) {
      if(threadIdx.y < INTERNAL_SIZE) {
        int forwardInd = threadIdx.y + index * INTERNAL_SIZE;
        while( forwardInd < rows) {
          buffer[threadIdx.y] += mat[i  + forwardInd * leading];
          index ++;
          forwardInd = threadIdx.y + index * INTERNAL_SIZE;
        }
      }
    }
    __syncthreads();

    int total = INTERNAL_SIZE > rows ? rows : INTERNAL_SIZE;
    while(total > 1) {
      int halfPoint = ((1+total) >> 1);
      if (threadIdx.y < halfPoint)  {
        float temp = 0.0;
        if(threadIdx.y+halfPoint < total) {
          temp = buffer[threadIdx.y + halfPoint];
        }
        buffer[threadIdx.y] += temp;
      }
      __syncthreads();
      total = ((1+total) >> 1);
    }
    __syncthreads();

    if(threadIdx.y == 0)
      vec[i]  = alpha* vec[i] + beta * buffer[0];
  }'''
  )

  add_col_sum_func = mod.get_function('add_col_sum')
  #block = (1, 1, 1)
  #grid = (mat.shape[0], 1, 1)

  grid = (mw, 1)
  block = (1, mh, 1)
  leading = mat.strides[0] / 4
  add_col_sum_func(mat, F(alpha), vec, F(beta), I(leading), I(mh), I(mw), block = block, grid= grid)
  timer.end('add_col_sum_to_vec')


def same_reduce(target, vec):
  '''
  Return the number of same values in the same offset of two vecs
  '''
  timer.start()
  mod = SourceModule('''
    __global__
    void same(float* tgt, float* vec, float* tmp) {
      int i = threadIdx.x;
      if( tgt[i] == vec[i] )
        tmp[i] = 1;
      else
        tmp[i] = 0;

    }'''
    )

  block = (target.size, 1, 1)
  grid = (1, 1)
  same_func = mod.get_function('same')
  tmp = gpuarray.zeros_like(target);
  same_func(target, vec, tmp, block = block, grid = grid)
  tmp.shape = (1, tmp.size)
  res = gpuarray.to_gpu(np.zeros((1,1)).astype(np.float32))
  add_row_sum_to_vec(res, tmp)
  timer.end('same_reduce')
  return int(res.get()[0, 0])

def logreg_cost_row_reduce(mat, label, cost):
  timer.start()
  mh, mw = mat.shape
  vh, vw = label.shape
  assert(vh == 1 and vw == mh or vw == 1 and vh == mh)

  mod = SourceModule('''
    __global__
    void log_reg(float* mat, float* label, float* cost, int leading){
      int i = threadIdx.x;
      int idx = i * leading + label[i];
      cost[i] = 0 - __logf(mat[idx]);
    }'''
    )

  log_reg_func = mod.get_function('log_reg')
  block = (mh, 1, 1)
  grid = (1, 1)
  log_reg_func(mat, label, cost, np.int32(mat.strides[0]/4), block = block, grid = grid)
  timer.end('logreg_cost_to_row_reduce')


def logreg_cost_col_reduce(mat, label, cost):
  timer.start()
  mh, mw = mat.shape
  vh, vw = label.shape
  assert(vh == 1 and vw == mw or vw == 1 and vh == mw)

  mod = SourceModule('''
    __global__
    void log_reg(float* mat, float* label, float* cost, int leading){
      int i = threadIdx.x;
      int idx = i + label[i] * leading;
      cost[i] = 0 - __logf(mat[idx]);
    }'''
    )

  log_reg_func = mod.get_function('log_reg')
  block = (mw,1,1)
  grid = (1, 1)
  log_reg_func(mat, label, cost, np.int32(mat.strides[0]/4), block = block, grid = grid)
  timer.end('logreg_cost_to_col_reduce')



def softmax_bprop(mat, label, grad):
  timer.start()
  mh, mw = mat.shape
  vh, vw = label.shape

  assert(vh == 1 and vw == mw or vw == 1 and vh  == mw)

  mod = SourceModule(
      '''
      __global__
      void softmax_bprop_grad(float* mat, float* label, float* grad, int leading, int rows, int cols){
        int i = blockDim.x * blockIdx.x + threadIdx.x;
        int j = blockDim.y * blockIdx.y + threadIdx.y;

        int idx= i + j * leading;
        if( i >= cols) return;
        if( j >= rows) return;

        if(j == label[i])
          grad[idx] = 1 - mat[idx];
        else
          grad[idx] = 0 - mat[idx];
      }
      '''
      )
  softmax_bprop_func = mod.get_function('softmax_bprop_grad')
  block = (32, 32, 1)
  grid = (NVBLOCK(mw, 32), NVBLOCK(mh, 32))
  softmax_bprop_func(mat, label, grad, I(mat.strides[0]/4), I(mh), I(mw), block = block, grid = grid)
  timer.end('softmax_bprop')

def relu_activate(input, output):
  timer.start()
  mh, mw = input.shape

  mod = SourceModule('''
  __global__
  void relu_activate(float* input, float* output, int leading, int rows, int cols) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    int j = blockIdx.y * blockDim.y + threadIdx.y;

    if(i >= cols) return ;
    if(j >= rows) return ;

    int idx = i + j * leading;

    output[idx] = fmaxf(input[idx], 0.0);
  }'''
  )
  relu_activate_func = mod.get_function('relu_activate')
  block = (32,32,1)
  grid = (NVBLOCK(mw, 32), NVBLOCK(mh, 32))
  leading = input.strides[0]/4
  relu_activate_func(input, output, I(leading), I(mh), I(mw), block = block , grid = grid)
  '''
  relu_func = ElementwiseKernel(
      'float *x, float *y',
      'y[i] = fmaxf(x[i], 0.0)',
      'relu_activation')
  relu_func(input, output)
  '''
  timer.end('relu_activate')


def relu_compute_grad(grad, output, outGrad):
  timer.start()
  mh, mw = grad.shape
  mod = SourceModule('''
  __global__
  void relu_compute_grad(float * grad, float * output, float* outGrad, int leading, int rows, int
  cols) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    int j = blockIdx.y * blockDim.y + threadIdx.y;

    if(i >= cols) return;
    if(j >= rows) return;

    int idx = i + j * leading;
    grad[idx] = grad[idx] * (output[idx] > 0.0f);
    outGrad[idx] = grad[idx];
  }
  ''')
  relu_compute_grad_func = mod.get_function('relu_compute_grad')
  block = (32, 32, 1)
  grid = (NVBLOCK(mw, 32), NVBLOCK(mh, 32))
  leading = grad.strides[0] / 4
  relu_compute_grad_func(grad, output, outGrad, I(leading), I(mh), I(mw), block = block, grid =
      grid)
  '''
  relu_grad_func  = ElementwiseKernel(
      'float *x, float* y, float* z',
      'x[i] = x[i] * (y[i] >  0.0f); z[i] = x[i]',
      'relu_gradient'
      )
  relu_grad_func(grad, output, outGrad)
  '''
  timer.end('relu_compute_grad')

def gpu_copy_to(x, y):
  timer.start()
  pycuda.driver.memcpy_dtod(y.gpudata, x.gpudata, x.nbytes)
  timer.end("gpu_copy_to")

def dot(x,y):
  timer.start()
  if isinstance(x, GPUArray):
    assert isinstance(y, GPUArray)
    if x.shape == (1,):
      assert y.shape[0] == 1
      y *= scalar(x)
      return y.ravel()
    elif y.shape == (1,):
      assert x.shape[1] == 1
      x *= scalar(y)
      return x.ravel()
    elif len(x.shape) == 1 and len(y.shape) == 1:
      return scalar(pycuda.gpuarray.dot(x,y))
    else:
      needs_ravel = False
      if len(x.shape) == 1:
        needs_ravel = True
        x = x.reshape((1,) + x.shape)
      if len(y.shape) == 1:
        needs_ravel = True
        y = y.reshape(y.shape + (1,))
      result = scikits.cuda.linalg.dot(x,y)
      if needs_ravel:
        assert result.shape[1] == 1 or result.shape[0] == 1
        result = result.ravel()
      timer.end('dot')
      return result
  else:
    return np.dot(x,y)

def transpose(mat):
  '''
  if isinstance(X, GPUArray):
    timer.start()
    b = scikits.cuda.linalg.transpose(X)
    timer.end('transpose')
    return b
  else:
    return X.T
  '''
  timer.start()
  mh, mw = mat.shape
  dst = gpuarray.empty((mw, mh), dtype = np.float32)
  mod = SourceModule('''
  __global__
  void transpose(float * src, float* dst, int sleading, int dleading, int srows, int scols) {
    int i = blockDim.x * blockIdx.x + threadIdx.x;
    int j = blockDim.y * blockIdx.y + threadIdx.y;

    if(i >= scols) return ;
    if(j >= srows) return ;

    int sind = i + j * sleading;
    int dind = j + i * dleading;

    dst[dind] = src[sind];
  }'''
  )

  transpose_func = mod.get_function('transpose')
  block = (32, 32, 1)
  grid = (NVBLOCK(mw, 32), NVBLOCK(mh, 32))
  sleading = mat.strides[0]/4
  dleading = dst.strides[0]/4
  transpose_func(mat, dst, I(sleading), I(dleading), I(mh), I(mw), block = block, grid = grid)

  timer.end('transpose')
  return dst



def matrix_add(src, v, dest = None, alpha = 1.0, beta = 1.0):
  sh, sw = src.shape
  vh, vw = v.shape

  assert sh == vh and sw == vw

  mod = SourceModule('''
  __global__
  void matrix_add(float* src, float* v, float* dest, float alpha, float beta,  int leading, int
  rows, int cols) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    int j = blockIdx.y * blockDim.y + threadIdx.y;

    if(i >= cols) return ;
    if(j >= rows) return ;

    int idx = i + j * leading;

    dest[idx] = src[idx] * alpha + v[idx] * beta;
  }'''
  )

  matrix_add_func = mod.get_function('matrix_add')
  block = (32, 32, 1)
  grid = (NVBLOCK(sw, 32), NVBLOCK(sh, 32))
  leading = src.strides[0] / 4
  if dest is None:
    dest = src
  matrix_add_func(src, v, dest, F(alpha), F(beta), I(leading), I(sh), I(sw), block = block , grid =
      grid)
