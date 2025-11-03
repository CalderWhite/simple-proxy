package simpleproxy

// Note to self: Just forward the auth from the requester to the workers.
// In this way, this is just an unauthenticated proxy and the remote server is randomly selected... ?

// Ultimately decided not to write the load balancer since I'd need to figure
// out private networking to avoid double-charge on bandwidth

type LoadBalancer struct {
}
