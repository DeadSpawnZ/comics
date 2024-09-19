from comics.models import Collector


def login(request):
    m = Collector.objects.get(username=request.POST["username"])
    if m.check_password(request.POST["password"]):
        request.session["member_id"] = m.id
        return HttpResponse("You're logged in.")
    else:
        return HttpResponse("Your username and password didn't match.")


def create_user(request):
    username
    password
    email
    first_name
    last_name
