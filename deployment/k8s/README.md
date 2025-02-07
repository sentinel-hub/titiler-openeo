## k8s / Helm Deployment

Try locally

```
# set up minikube locally
minikube start
kubectl config use-context minikube

# run in the k8s directory
helm upgrade --install titiler-openeo .

# then you may want to get the ingress
minikube addons enable ingress

# obtain the ip and port the service is locally available at
minikube service ingress-nginx-controller -n ingress-nginx --url | head -n 1
```
