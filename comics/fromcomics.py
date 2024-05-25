from comics.models import Publishing, Editorial, Title, Printing


ps = [
['All The New Exiles', 'Malibu'],
['Artbook Spawn','La Mole'],
['Gunslinger','Image'],
['Cyberforce Universe Sourcebook','Image'],
['Cyberforce Universe Sourcebook','Image'],
["Devil's Reign Cyber Force",'Marvel / Top Cow'],
['Felicia','Udon'],
['Gen 13 Monkey O Brian','Image / Dark Horse'],
['Grifter Shi','Image'],
['Superman Thundercats','DC / WildStorm'],
['The Magdalena Vampirella','Image'],
['The Magdalena Vampirella','Top Cow'],
['The Phoenix Resurrection Chapter Five','Marvel'],
['Vampirella Red Sonja','Dynamite'],
['Vampirella Shi','Harris Comics'],
['Shi Vampirella',' Crusade']]


already_in = []

for p in ps:
    if p[0] and p[1]:
        try:
            if p[0] in already_in:
                continue
            already_in.append(p[0])
            editoriales = p[1].split('/')
            editoriales = [i.strip() for i in editoriales]
            f_editorials = []
            for edit in editoriales:
                f_editorials.append(Editorial.objects.get(name=edit))
            
            temp = Publishing(
                language='en',
                publishing_title=p[0],
                serie='1st',
                printing=Printing.objects.get(name='1st'),
                title=Title.objects.get(name=p[0]),
                year=2024
            )
            temp.save()
            temp.editorials.set(f_editorials)
            temp.save()
        except Exception as ex:
            print(p[0])
            print(ex)
            continue