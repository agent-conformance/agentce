package example;

/* metamodel_version: 1.11.0 */
/* version: 0.1.0 */
import java.net.URI;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZonedDateTime;
import java.util.List;
import lombok.*;

@Data
@EqualsAndHashCode(callSuper=false)
public class SessionStartPayload extends Payload {

  private Principal deployer;
  private String environment;
  private String bundleDigest;
  private List<String> modelVersions;
  private String intendedPurposeRef;
  private String registrationRef;


}