Striate
=========
A simple implementation of a Convolutional Neural Network which is based on (and extends) the [LeNet5 implementation](http://deeplearning.net/tutorial/lenet.html) in Theano's tutorial. 
Though most of the code runs on the GPU (thanks Theano & PyCUDA!), it's still not nearly as fast as you could make it with hand-rolled kernels. Also, the provided training algorithm is just mini-batch 
stochastic gradient descent. I'm using this library as a testbed for experimenting with other learning algorithms: the code is a sloppy prototype cobbled together from other examples and 
the features are the minimum I need for my own needs. You've been warned! 

* If you want top-notch runtime and accuracy, go talk to [Alex K](https://code.google.com/p/cuda-convnet/).

* If you want to know about convolutional neural networks, go talk to [Yann LeCun](http://yann.lecun.com/). 

**Requires**
  
  * CUDA
  * Theano
  * PyCUDA
  * scikits.cuda 
  * NumPy
  * SciPy
